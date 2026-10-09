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
import logging
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
CAP_FILE = "spend_cap.json"
PHOTO_DIR = "photo-checks"


class Ticket:
    def __init__(self, user_id, reservation):
        self.user_id, self.reservation = user_id, reservation


class CheckGate:
    def __init__(self, counter, cap):
        self.counter, self.cap = counter, cap
        self._local = threading.local()

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
        """The compare chain's per-call hook: add the call's cost to the open reservation."""
        ticket = self._active()
        if ticket is None:
            log.error("spend_cap_error code=NO_RESERVATION")
            return
        ticket.reservation.record(cost)

    def guard(self, compare):
        """compare that refuses to run without an open reservation (no provider call)."""
        def guarded(reference, candidate):
            if self._active() is None:
                log.error("spend_cap_error code=NO_RESERVATION")
                return {"status": "UNAVAILABLE"}
            return compare(reference, candidate)
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
        cap = spend_cap.SpendCap(os.path.join(data_dir, CAP_FILE), spend_cap.cap_from_env(environ),
                                 per_call_fallback_usd=PER_CALL_FALLBACK_USD,
                                 calls_per_check=CALLS_PER_CHECK, **extra)
    except spend_cap.SingleWriterError:
        log.error("spend_cap_error code=SECOND_WRITER")
        return None
    except spend_cap.CapConfigError:
        log.error("spend_cap_error code=CONFIG")
        return None
    gate.counter, gate.cap = counter, cap
    factory = session_factory.make_session_factory(api_get_file, api_fetch, root, MAX_IMAGE_BYTES)
    log.info("vision enabled")
    return PhotoChecks(factory, gate.guard(compare), gate=gate)
