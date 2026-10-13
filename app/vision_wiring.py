"""WP-BOT-05: turn the vision check on in the bot, fail closed. Standard library only.

build_photo_checks(environ, api_get_file, api_fetch, ...) -> PhotoChecks or None

None (the bot behaves as before) unless every piece is in place, in this order:
the VISION_* config (compare_from_env), CHECK_COUNTER_SALT (>= 32 characters), a
readable free-check counter, and the spend cap: ONE SpendCap per process, created
here at startup; CapConfigError / SingleWriterError turn the feature off.

CheckGate does the per-check order (D-12, D-11, D-7, WP-BOT-03):
counter allows -> uncharged daily limit (WP-BOT-10, D-15) -> cap.reserve() before the first provider call -> compare, with
reservation.begin() before and record(usage.cost) after each provider call (spend cap v2,
D-17: the hold and each request mark are on disk before anything leaves) -> the counter is
charged only on MATCH or MISMATCH -> reservation.close() in a finally. At startup every
hold a previous process left on the ledger is logged as spend_cap_orphan.

Logs carry codes only: no user id, counter key, file id, path, bytes or model text.
"""
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import datetime as _dt

from app import check_counter, session_factory, spend_cap
from app.photo_checks import PhotoChecks
from app.vision_compare import VisionConfigError, compare_from_env
from app.vision_request import MAX_IMAGE_BYTES

log = logging.getLogger("vision_wiring")

CHARGED = ('MATCH', 'MISMATCH')
PER_CALL_FALLBACK_USD = 0.02
CALLS_PER_CHECK = 3
_MICRO = 1_000_000
HOLD_MICRO = round(PER_CALL_FALLBACK_USD * _MICRO) * CALLS_PER_CHECK


def _micro(cost):
    """Use the cap's conversion so both accounting paths agree (P6, theone)."""
    amount = spend_cap._money(cost)
    return round(PER_CALL_FALLBACK_USD * _MICRO) if amount is None else amount
UNCHARGED_LIMIT_ENV = "UNCHARGED_DAILY_LIMIT"
DEFAULT_UNCHARGED_LIMIT = 10
CAP_FILE = "spend_cap.json"
_HOLD_ID_RE = re.compile(r"^[0-9a-f]{32}$")     # spend_cap v2 hold ids are uuid4().hex
_CODE_RE = re.compile(r"^[A-Z_]{1,32}$")         # spend_cap fail_code values are fixed codes
PHOTO_DIR = "photo-checks"


class Ticket:
    def __init__(self, user_id, reservation):
        self.user_id, self.reservation = user_id, reservation
        self.spent_micro, self.overrun = 0, False


def _ledger_micro(path):
    """Today's total from the spend cap ledger, read only (record() has just written it).

    app/spend_cap.py has no public getter and stays verbatim, so this reads the
    "spent_usd" key of its file (v1 {"day", "spent_usd"}; v2 adds "holds", which are
    not spent and not read here). Unreadable or malformed -> None (the caller stops the check).
    """
    try:
        with open(path, "rb") as f:
            spent = json.loads(f.read().decode("utf-8"))["spent_usd"]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if isinstance(spent, bool) or not isinstance(spent, (int, float)) or not math.isfinite(spent):
        return None
    return round(spent * _MICRO)


def uncharged_limit_from_env(environ):
    """UNCHARGED_DAILY_LIMIT: a positive integer; unset or empty -> 10; invalid -> 10, logged by code."""
    raw = str(environ.get(UNCHARGED_LIMIT_ENV) or "").strip()
    if not raw:
        return DEFAULT_UNCHARGED_LIMIT
    if raw.isascii() and raw.isdigit() and int(raw) >= 1:
        return int(raw)
    log.warning("uncharged_limit_config code=INVALID")
    return DEFAULT_UNCHARGED_LIMIT


class CheckGate:
    """Per-check gate. The uncharged-check counts (WP-BOT-10) live in memory only, keyed by
    the counter's HMAC key (never the raw id), for the current UTC day; nothing is written
    to disk and a restart resets them."""

    def __init__(self, counter, cap, *, cap_usd=None, ledger_path=None,
                 uncharged_limit=DEFAULT_UNCHARGED_LIMIT, clock=time.time):
        self.counter, self.cap = counter, cap
        self.cap_usd, self.ledger_path = cap_usd, ledger_path
        self.uncharged_limit, self._clock = uncharged_limit, clock
        self._uncharged = {}          # HMAC key -> uncharged checks today
        self._uncharged_day = None
        self._ulock = threading.Lock()
        self._local = threading.local()

    def _roll_day(self):
        """Under _ulock: drop all counts when the UTC day changed."""
        today = _dt.datetime.fromtimestamp(self._clock(), _dt.timezone.utc).date()
        if self._uncharged_day != today:
            self._uncharged, self._uncharged_day = {}, today

    def _uncharged_today(self, user_id):
        key = self.counter.key(user_id)
        with self._ulock:
            self._roll_day()
            return self._uncharged.get(key, 0)

    def _count_uncharged(self, user_id):
        key = self.counter.key(user_id)
        with self._ulock:
            self._roll_day()
            self._uncharged[key] = self._uncharged.get(key, 0) + 1

    def _over_cap(self):
        """Today's recorded total past the cap (or unknown)."""
        if self.ledger_path is None or self.cap_usd is None:
            return False
        day = _ledger_micro(self.ledger_path)
        return day is None or day > round(self.cap_usd * _MICRO)

    def _active(self):
        return getattr(self._local, "ticket", None)

    def admit(self, user_id):
        """(Ticket, None) or (None, refusal text key). Never raises."""
        try:
            self.counter.maybe_purge()
            if not self.counter.allows(user_id):
                log.info("check_refused code=FREE_USED_UP")
                return None, 'free_used_up'
            if self._uncharged_today(user_id) >= self.uncharged_limit:
                # The outcome is unknown before the call, so ANY check is refused today.
                log.info("check_refused code=UNCHARGED_LIMIT")
                return None, 'uncharged_limit'
            reservation = self.cap.reserve()
            if reservation is None:
                log.info("check_refused code=DAILY_CAP")
                return None, 'limit_reached'
        except Exception:
            log.error("check_gate_error code=ADMIT")
            return None, 'UNAVAILABLE'
        ticket = Ticket(user_id, reservation)
        self._local.ticket = ticket
        return ticket, None

    def record_call(self, cost):
        """The compare chain's per-call hook: add the call's cost to the open reservation.

        Returns False (stop the check) when this check's recorded total is past its
        hold (3 x 0.02 USD) or today's recorded total is past the cap (board post
        0008681a); the check then ends UNAVAILABLE and is not charged.
        """
        ticket = self._active()
        if ticket is None:
            log.error("spend_cap_error code=NO_RESERVATION")
            return False
        ticket.reservation.record(cost)
        ticket.spent_micro += _micro(cost)
        if ticket.spent_micro > HOLD_MICRO or self._over_cap():
            if not ticket.overrun:
                log.warning("spend_cap_overrun")
            ticket.overrun = True
        return not ticket.overrun

    def begin_call(self):
        """The compare chain's pre-send hook (spend cap v2, D-17): mark one request as
        leaving on the ledger. False -> the request is not sent and the check ends
        UNAVAILABLE, not charged."""
        ticket = self._active()
        if ticket is None:
            log.error("spend_cap_error code=NO_RESERVATION")
            return False
        if ticket.reservation.begin():
            return True
        log.error("spend_cap_error code=BEGIN")
        return False

    def not_sent_call(self):
        """The request marked by begin_call provably never left (the transport was not
        invoked): release its mark. Never used on a provider error (T3b cannot prove it)."""
        ticket = self._active()
        if ticket is not None:
            ticket.reservation.not_sent()

    def guard(self, compare):
        """compare that refuses to run without an open reservation (no provider call)."""
        def guarded(reference, candidate):
            ticket = self._active()
            if ticket is None:
                log.error("spend_cap_error code=NO_RESERVATION")
                return {"status": "UNAVAILABLE"}
            result = compare(reference, candidate)
            if ticket.overrun:
                return {"status": "UNAVAILABLE"}   # never a charged verdict past the hold
            return result
        return guarded

    def settle(self, ticket, status, refused=False):
        """Charge the free-check counter on MATCH/MISMATCH only; always close the reservation,
        even when charge() raises a BaseException (WP-BOT-10 review, WP-BOT-12 3a).
        An image refused before any call (refused=True) is not an uncharged check for the
        daily limit (WP-BOT-14 1c): the seller only has to send another file."""
        try:
            try:
                if status in CHARGED:
                    self.counter.charge(ticket.user_id)
            except Exception:
                log.error("check_counter_error code=CHARGE")
            try:
                if status not in CHARGED and not refused:
                    self._count_uncharged(ticket.user_id)
            except Exception:
                log.error("check_gate_error code=UNCHARGED_COUNT")
        finally:
            if self._active() is ticket:
                self._local.ticket = None
            ticket.reservation.close()
        log.info("check_done status=%s charged=%s", status if status else "NONE",
                 "yes" if status in CHARGED else "no")

    def forget(self, user_id):
        """/forget: delete the user's counter row (raises when that failed) and in-memory count."""
        with self._ulock:
            self._uncharged.pop(self.counter.key(user_id), None)
        self.counter.forget(user_id)

    def tick(self):
        try:
            self.counter.maybe_purge()
        except Exception:
            log.error("check_counter_error code=PURGE")


def build_photo_checks(environ, api_get_file, api_fetch, *, data_dir=None, photo_dir=None,
                       compare_factory=compare_from_env, clock=None):
    """PhotoChecks with the real session factory, the counter and one spend cap, or None."""
    gate = CheckGate(None, None)
    try:
        compare = compare_factory(environ, on_call=gate.record_call, on_begin=gate.begin_call,
                                  on_not_sent=gate.not_sent_call)
    except VisionConfigError as exc:
        log.warning("vision disabled, config invalid: %s", exc)
        return None
    if compare is None:
        return None
    salt = check_counter.salt_from_env(environ)
    if salt is None:
        return None
    data_dir = data_dir or environ.get("DATA_DIR") or "/data"
    photo_dir = photo_dir or os.path.join(tempfile.gettempdir(), PHOTO_DIR)
    extra = {} if clock is None else {"clock": clock}
    try:
        os.makedirs(data_dir, exist_ok=True)
        counter = check_counter.CheckCounter(os.path.join(data_dir, check_counter.FILE_NAME), salt, **extra)
    except check_counter.CounterConfigError:
        log.error("check_counter_error code=CONFIG")
        return None
    except OSError:
        log.error("check_counter_error code=DATA_DIR")
        return None
    try:
        root = session_factory.prepare_root(photo_dir)
    except OSError:
        log.error("vision disabled, code=PHOTO_DIR")
        return None
    try:
        cap_usd = spend_cap.cap_from_env(environ)
        cap = spend_cap.SpendCap(os.path.join(data_dir, CAP_FILE), cap_usd,
                                 per_call_fallback_usd=PER_CALL_FALLBACK_USD,
                                 calls_per_check=CALLS_PER_CHECK, **extra)
    except spend_cap.SingleWriterError:
        log.error("spend_cap_error code=SECOND_WRITER")
        return None
    except spend_cap.CapConfigError:
        log.error("spend_cap_error code=CONFIG")
        return None
    # Startup reconciliation (D-17): holds a previous process left on the ledger count in
    # full for their day until an operator settles them (docs/t4-vision.md); never here.
    for hold_id, day, usd in cap.orphans():
        log.warning("spend_cap_orphan hold=%s day=%s usd=%.6f",
                    hold_id if _HOLD_ID_RE.match(str(hold_id)) else "INVALID", day, usd)
    if cap.state != "ready":
        # The ledger is unusable (LOCK_WITHOUT_LEDGER, LEDGER_CORRUPT, ...): every reserve()
        # would refuse, so keep the feature off and say so at startup ("vision enabled" is
        # absent). Recovery: docs/t4-vision.md, Spend cap.
        code = cap.fail_code
        log.error("vision disabled, code=%s", code if _CODE_RE.match(str(code)) else "INTERNAL")
        cap.close()
        return None
    gate.counter, gate.cap = counter, cap
    gate.cap_usd, gate.ledger_path = cap_usd, os.path.join(data_dir, CAP_FILE)
    gate.uncharged_limit = uncharged_limit_from_env(environ)
    if clock is not None:
        gate._clock = clock
    factory = session_factory.make_session_factory(api_get_file, api_fetch, root, MAX_IMAGE_BYTES)
    log.info("vision enabled")
    return PhotoChecks(factory, gate.guard(compare), gate=gate)
