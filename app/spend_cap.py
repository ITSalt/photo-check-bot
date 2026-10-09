"""Daily spend cap for a vision-API bot, v2.1.1: holds are durable. Python 3.12, standard library only.

Money is integer micro-dollars inside. One process owns the ledger (non-blocking flock on
<path>.lock, kept until close()). Every hold is written to the ledger, atomically, before
reserve() returns it, so no request can leave before its liability is on disk.

A hold is freed only by evidence:
- record(cost) books a real charge (the hold shrinks by it);
- close() frees what is left only if no request is in flight (every begin() matched by a
  record() or a not_sent()); otherwise the hold stays on the ledger as an orphan;
- a hold found on the ledger when a new SpendCap starts is an orphan: counted in full for
  the day it was taken, until settle(hold_id, cost) books the real charge for it.
A live hold counts until it is freed, even past midnight. An orphan stops counting only when its
day is over AND it is older than orphan_lifetime_s (default 600 s; None = never, only settle()
frees it). The lifetime is housekeeping, not proof: choose it >= 2x the client timeout plus how long
the provider may still bill after the client gave up, which this module cannot know.

What the cap promises, as two separate claims:
A. Authorization: over any N days at most N caps are authorized. Each hold is admitted against the
   day it was taken, so its late charge was paid for out of that day's budget. True if no call costs
   more than its slice of the hold (per_call_fallback_usd) and a restart cannot reuse a hold.
   Nothing about time is assumed of the provider.
B. The provider's bill for one calendar day: at most the cap plus the holds in flight across
   midnight. Which day the provider books a late charge to is the provider's rule. A late settle()
   books to today: it over-counts, never under-counts.
state is "ready" or "failed"; fail_code says why (LEDGER_CORRUPT, LOCK_WITHOUT_LEDGER,
LEDGER_CHANGED, LEDGER_UNREADABLE, CLOCK_BACKWARDS, CLOSED). Reading it checks the ledger now.
Fails closed: an unreadable, changed or deleted ledger, a clock that went back past the ledger's day,
a closed SpendCap, a failed write or any internal error means reserve() returns None and begin()
returns False. Costs that could not be written stay counted in memory and are logged with the hold
id and amount. To start from nothing, delete both <path> and <path>.lock."""
import fcntl, json, logging, math, os, tempfile, threading, time, uuid, datetime as _dt

log = logging.getLogger("spend_cap")
_M = 1_000_000                                  # micro-dollars per dollar


class CapConfigError(Exception):
    pass


class SingleWriterError(CapConfigError):
    pass


class _LedgerCorrupt(Exception):
    def __init__(self, code="LEDGER_CORRUPT"):
        super().__init__(code)
        self.code = code


def _money(x):
    """A finite, non-negative real number (not bool) -> micro-dollars; anything else -> None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if not math.isfinite(x) or x < 0:
        return None
    return round(x * _M)


def _day(s):
    if not isinstance(s, str):
        raise _LedgerCorrupt
    try:
        d = _dt.date.fromisoformat(s).isoformat()
    except ValueError:
        raise _LedgerCorrupt from None
    if d != s:
        raise _LedgerCorrupt
    return d


def cap_from_env(environ):
    raw = environ.get("VISION_DAILY_CAP_USD")
    if raw is None or raw.strip() == "":
        return 2.00
    try:
        v = float(raw)
    except ValueError:
        raise CapConfigError("VISION_DAILY_CAP_USD is not a number") from None
    if not math.isfinite(v) or v < 0:
        raise CapConfigError("VISION_DAILY_CAP_USD must be a finite number >= 0")
    return v


def _utc_day(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).date().isoformat()


def _taken_at(h):
    """When the hold was taken; a v2 hold (no "at") is read as the end of its day, never stored so."""
    if h.at is not None:
        return h.at
    return _dt.datetime.fromisoformat(h.day).replace(tzinfo=_dt.timezone.utc).timestamp() + 86400


class _Hold:
    __slots__ = ("id", "day", "left", "in_flight", "orphan", "at")

    def __init__(self, hid, day, left, in_flight=0, orphan=False, at=None):
        self.id, self.day, self.left, self.in_flight, self.orphan, self.at = hid, day, left, in_flight, orphan, at


class SpendCap:
    def __init__(self, path, cap_usd, *, per_call_fallback_usd=0.02, calls_per_check=3, clock=time.time,
                 orphan_lifetime_s=600):
        cap, fb = _money(cap_usd), _money(per_call_fallback_usd)
        if cap is None:
            raise CapConfigError("cap_usd must be a finite number >= 0")
        if fb is None:
            raise CapConfigError("per_call_fallback_usd must be a finite number >= 0")
        if isinstance(calls_per_check, bool) or not isinstance(calls_per_check, int) or calls_per_check < 1:
            raise CapConfigError("calls_per_check must be an integer >= 1")
        if orphan_lifetime_s is not None and _money(orphan_lifetime_s) is None:
            raise CapConfigError("orphan_lifetime_s must be a finite number >= 0, or None for never")
        self._lifetime = orphan_lifetime_s
        self._path = os.path.abspath(os.fspath(path))
        self._cap, self._fallback, self._hold_size = cap, fb, fb * calls_per_check
        self._clock, self._now = clock, None
        self._lock = threading.Lock()
        self._closed = False
        self._loaded = False                     # memory mirrors the ledger once loaded
        self._day, self._spent, self._holds = None, 0, {}
        self._on_disk = None                     # bytes of our last good write (or of the file we loaded)
        fresh = not os.path.exists(self._path + ".lock")
        try:
            self._lock_fd = os.open(self._path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            raise CapConfigError("cannot open the spend cap lock file") from None
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise SingleWriterError("another process holds the spend cap") from None
        self._fresh = fresh and not os.path.exists(self._path)
        if self._fresh:                          # write the ledger now: from here on a missing one means deleted
            try:
                with self._lock:
                    self._change(lambda today: None, must_persist=True)
            except Exception:
                self.close()
                raise CapConfigError("cannot write the spend cap ledger") from None

    # -- single-writer lock
    def close(self):
        """Release the flock. Open holds stay on the ledger: the next owner counts them as orphans."""
        with self._lock:
            self._closed = True                  # every later operation fails closed
            if self._lock_fd is not None:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- ledger (caller holds self._lock)
    def _today(self):
        self._now = self._clock()               # one reading per operation: a hold's "at" and "day" agree
        return _utc_day(self._now)

    def _parse(self, raw, today):
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise _LedgerCorrupt from None
        if not isinstance(data, dict) or set(data) not in ({"day", "spent_usd"}, {"day", "spent_usd", "holds"}):
            raise _LedgerCorrupt                 # a v1 file (no "holds") is read as having none
        day, spent = _day(data["day"]), _money(data["spent_usd"])
        if spent is None or day > today:         # a future day is not trusted
            raise _LedgerCorrupt
        holds = {}
        raw_holds = data.get("holds", {})
        if not isinstance(raw_holds, dict):
            raise _LedgerCorrupt
        for hid, h in raw_holds.items():
            if not isinstance(h, dict) or set(h) not in ({"day", "usd", "in_flight"}, {"day", "usd", "in_flight", "at"}):
                raise _LedgerCorrupt
            hday, left, n = _day(h["day"]), _money(h["usd"]), h["in_flight"]
            if left is None or hday > today or isinstance(n, bool) or not isinstance(n, int) or n < 0:
                raise _LedgerCorrupt
            if "at" in h:
                at = h["at"]
                if _money(at) is None or _utc_day(at) != hday:
                    raise _LedgerCorrupt
            else:                                # a v2 hold: no "at" in memory or on disk; _roll() reads
                at = None                        # it as the end of its day
            holds[hid] = _Hold(hid, hday, left, n, orphan=True, at=at)   # a previous owner's hold
        return day, spent, holds

    def _load(self, today):
        """First use: read the ledger left by any previous owner. Later: check nobody changed it."""
        try:
            with open(self._path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            raw = None
        except OSError:
            raise _LedgerCorrupt("LEDGER_UNREADABLE") from None
        if self._loaded:
            if raw != self._on_disk:
                raise _LedgerCorrupt("LEDGER_CHANGED")
            return
        if raw is None and not self._fresh:
            raise _LedgerCorrupt("LOCK_WITHOUT_LEDGER")   # the lock file outlived its ledger: it was deleted
        pend_day, pend = self._day, self._spent     # costs recorded while the ledger was unreadable
        if raw is not None:
            self._day, self._spent, self._holds = self._parse(raw, today)
        self._on_disk, self._loaded = raw, True
        if pend and pend_day == self._day:
            self._spent += pend
        elif pend and (raw is None or pend_day > self._day):
            self._day, self._spent = pend_day, pend

    def _roll(self, today):
        if self._day != today:
            self._day, self._spent = today, 0
        if self._lifetime is None:
            return                               # orphans wait for settle()
        now = self._now
        for hid in [h.id for h in self._holds.values()
                    if h.orphan and h.day < today and now - _taken_at(h) > self._lifetime]:
            log.warning("spend_cap_orphan_expired hold=%s usd=%.6f", hid, self._holds[hid].left / _M)
            del self._holds[hid]                 # an old day's orphan that can no longer bill

    def _total(self, today):
        return self._spent + sum(h.left for h in self._holds.values())   # a check spanning midnight still counts

    def _encode(self):
        holds = {}
        for h in self._holds.values():
            holds[h.id] = {"day": h.day, "usd": h.left / _M, "in_flight": h.in_flight}
            if h.at is not None:                 # a v2 hold is written back as the old owner wrote it
                holds[h.id]["at"] = h.at
        return json.dumps({"day": self._day, "spent_usd": self._spent / _M, "holds": holds}).encode("utf-8")

    def _write(self):
        raw = self._encode()
        d = os.path.dirname(self._path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".spend_cap.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
            dfd = os.open(d, os.O_RDONLY)        # make the rename itself durable
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._on_disk = raw

    def _change(self, apply, must_persist, what=""):
        """Load/verify, roll the day, apply a change to memory, write it. Returns apply()'s result.
        must_persist: undo the change and raise if the write fails (reserve, begin).
        what: hold id and amount, logged when a change stays in memory only."""
        today = self._today()
        code = None
        if self._closed:
            code = "CLOSED"
        elif self._day is not None and today < self._day:
            code, today = "CLOCK_BACKWARDS", self._day   # never let a clock step reopen a spent day
        else:
            try:
                self._load(today)
            except _LedgerCorrupt as e:
                code = e.code
        if code:
            log.error("spend_cap_error code=%s%s", code, what)
            if must_persist:
                raise _LedgerCorrupt
            self._roll(today)
            return apply(today)                  # keep the cost counted in memory; the file is left
                                                 # untouched for a human to look at
        self._roll(today)
        undo = (self._day, self._spent, {k: _Hold(h.id, h.day, h.left, h.in_flight, h.orphan, h.at)
                                          for k, h in self._holds.items()})
        out = apply(today)
        try:
            self._write()
        except OSError:
            log.error("spend_cap_error code=LEDGER_WRITE%s", what)
            if must_persist:
                self._day, self._spent, self._holds = undo
                raise
        return out

    # -- public
    def reserve(self):
        """A Reservation whose hold is already on disk, or None (cap reached, or any failure)."""
        try:
            with self._lock:
                def apply(today):
                    if self._total(today) + self._hold_size > self._cap:
                        return None
                    h = _Hold(uuid.uuid4().hex, today, self._hold_size, at=self._now)
                    self._holds[h.id] = h
                    return h
                try:
                    h = self._change(apply, must_persist=True)
                except (_LedgerCorrupt, OSError):
                    return None
                return None if h is None else Reservation(self, h.id)
        except Exception:
            log.error("spend_cap_error code=INTERNAL")
            return None

    @property
    def fail_code(self):
        """None if the ledger is usable right now, else why not. Reads the file; never writes it."""
        with self._lock:
            if self._closed:
                return "CLOSED"
            today = self._today()
            if self._day is not None and today < self._day:
                return "CLOCK_BACKWARDS"
            try:
                self._load(today)
            except _LedgerCorrupt as e:
                return e.code
            except Exception:
                return "INTERNAL"
            return None

    @property
    def state(self):
        """"ready" or "failed" (then see fail_code). A ready cap can still refuse: cap reached, write failed."""
        return "ready" if self.fail_code is None else "failed"

    def orphans(self):
        """[(hold_id, day, usd)] for holds left by a previous owner or closed with a request in flight."""
        with self._lock:
            if not self._loaded and not self._closed:
                try:
                    self._load(self._today())    # a new owner sees the old owner's holds before any reserve()
                except _LedgerCorrupt as e:
                    log.error("spend_cap_error code=%s", e.code)
            return sorted((h.id, h.day, h.left / _M) for h in self._holds.values() if h.orphan)

    def settle(self, hold_id, cost_usd):
        """Book the total real provider charge for an orphan hold (all its requests, from the provider's
        usage records) and drop the hold. Bad costs count as the fallback. Never raises. Returns
        "settled"; "unknown" (no such orphan, e.g. expired: the cost is still booked today); or
        "live" (the hold belongs to an open Reservation: nothing booked, use its record())."""
        amount = _money(cost_usd)
        if amount is None:
            amount = self._fallback
        try:
            with self._lock:
                def apply(today):
                    h = self._holds.get(hold_id)
                    if h is not None and not h.orphan:
                        return "live"
                    self._spent += amount
                    return "unknown" if self._holds.pop(hold_id, None) is None else "settled"
                return self._change(apply, must_persist=False, what=f" hold={hold_id} usd={amount / _M:.6f}")
        except Exception:
            log.error("spend_cap_error code=INTERNAL hold=%s usd=%.6f", hold_id, amount / _M)
            return "unknown"

    def _begin(self, hid):
        with self._lock:
            def apply(today):
                h = self._holds.get(hid)
                if h is None or h.orphan:
                    return False
                h.in_flight += 1
                return True
            try:
                return self._change(apply, must_persist=True)
            except (_LedgerCorrupt, OSError):
                return False

    def _record(self, hid, amount, sent):
        with self._lock:
            def apply(today):
                self._spent += amount
                h = self._holds.get(hid)
                if h is None:
                    return
                h.left -= min(amount, h.left)
                if sent and h.in_flight:
                    h.in_flight -= 1
                if h.orphan and h.in_flight == 0:   # closed early; its last request has now settled
                    del self._holds[hid]
            self._change(apply, must_persist=False, what=f" hold={hid} usd={amount / _M:.6f}")

    def _release(self, hid):
        with self._lock:
            def apply(today):
                h = self._holds.get(hid)
                if h is None or h.orphan:
                    return
                if h.in_flight == 0:
                    del self._holds[hid]
                else:
                    h.orphan = True                  # a request is out: its hold stays counted
            self._change(apply, must_persist=False, what=f" hold={hid} close")


class Reservation:
    """Use: r = cap.reserve(); for each call: if not r.begin(): stop; send; r.record(cost).
    If the send failed before any byte could reach the provider, call r.not_sent() instead."""

    def __init__(self, cap, hold_id):
        self._cap, self.hold_id, self._closed = cap, hold_id, False

    def begin(self):
        """Mark one request as leaving. Returns False (do not send) if that mark cannot be written."""
        if self._closed:
            return False
        try:
            return self._cap._begin(self.hold_id)
        except Exception:
            log.error("spend_cap_error code=INTERNAL")
            return False

    def record(self, cost_usd):
        """Add a real provider cost to today's total. Bad costs count as the fallback. Never raises."""
        amount = _money(cost_usd)
        if amount is None:
            amount = self._cap._fallback
        try:
            self._cap._record(self.hold_id, amount, sent=True)
        except Exception:
            log.error("spend_cap_error code=INTERNAL hold=%s usd=%.6f", self.hold_id, amount / _M)

    def not_sent(self):
        """The request marked by begin() provably never left (e.g. connection refused). Never raises."""
        try:
            with self._cap._lock:
                def apply(today):
                    h = self._cap._holds.get(self.hold_id)
                    if h is not None and h.in_flight:
                        h.in_flight -= 1
                        if h.orphan and h.in_flight == 0:
                            del self._cap._holds[self.hold_id]
                self._cap._change(apply, must_persist=False, what=f" hold={self.hold_id} not_sent")
        except Exception:
            log.error("spend_cap_error code=INTERNAL")

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._cap._release(self.hold_id)
            except Exception:
                log.error("spend_cap_error code=INTERNAL")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
