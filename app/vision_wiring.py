"""WP-BOT-05: turn the vision check on in the bot, fail closed. Standard library only.

build_photo_checks(environ, api_get_file, api_fetch, ...) -> PhotoChecks or None

None (the bot behaves as before) unless every piece is in place, in this order:
the VISION_* config (compare_from_env), CHECK_COUNTER_SALT (>= 32 characters), a
readable free-check counter, and the spend cap: ONE SpendCap per process, created
here at startup; CapConfigError / SingleWriterError turn the feature off.

CheckGate does the per-check order (D-12, D-11, D-7, WP-BOT-03):
counter allows -> cap.reserve() before the first provider call -> compare, with
record(usage.cost) after each provider call -> the counter is charged only on MATCH
or MISMATCH -> reservation.close() in a finally.

Logs carry codes only: no user id, counter key, file id, path, bytes or model text.
"""
import json
import logging
import math
import os
import tempfile
import threading

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
    """A call's cost in micro-dollars as the cap counts it: unusable -> the fallback."""
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        return round(PER_CALL_FALLBACK_USD * _MICRO)
    return round(cost * _MICRO)
CAP_FILE = "spend_cap.json"
PHOTO_DIR = "photo-checks"


class Ticket:
    def __init__(self, user_id, reservation):
        self.user_id, self.reservation = user_id, reservation
        self.spent_micro, self.overrun = 0, False


def _ledger_micro(path):
    """Today's total from the spend cap ledger, read only (record() has just written it).

    app/spend_cap.py has no public getter and stays verbatim, so this reads its
    {"day", "spent_usd"} file. Unreadable or malformed -> None (the caller stops the check).
    """
    try:
        with open(path, "rb") as f:
            spent = json.loads(f.read().decode("utf-8"))["spent_usd"]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if isinstance(spent, bool) or not isinstance(spent, (int, float)) or not math.isfinite(spent):
        return None
    return round(spent * _MICRO)


class CheckGate:
    def __init__(self, counter, cap, *, cap_usd=None, ledger_path=None):
        self.counter, self.cap = counter, cap
        self.cap_usd, self.ledger_path = cap_usd, ledger_path
        self._local = threading.local()

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

    def settle(self, ticket, status):
        """Charge the free-check counter on MATCH/MISMATCH only; always close the reservation."""
        try:
            if status in CHARGED:
                self.counter.charge(ticket.user_id)
        except Exception:
            log.error("check_counter_error code=CHARGE")
        finally:
            if self._active() is ticket:
                self._local.ticket = None
            ticket.reservation.close()
        log.info("check_done status=%s charged=%s", status if status else "NONE",
                 "yes" if status in CHARGED else "no")

    def forget(self, user_id):
        """/forget: delete the user's counter row (raises when that failed)."""
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
        compare = compare_factory(environ, on_call=gate.record_call)
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
    gate.counter, gate.cap = counter, cap
    gate.cap_usd, gate.ledger_path = cap_usd, os.path.join(data_dir, CAP_FILE)
    factory = session_factory.make_session_factory(api_get_file, api_fetch, root, MAX_IMAGE_BYTES)
    log.info("vision enabled")
    return PhotoChecks(factory, gate.guard(compare), gate=gate)
