"""Daily spend cap for a vision-API bot. Python 3.12, standard library only.

Money is kept as integer micro-dollars inside, so 33 holds of 0.06 are exactly 1.98
and the 34th is refused without float drift. The ledger file holds recorded costs only;
holds of open reservations live in memory. One process owns the ledger: the constructor
takes a non-blocking flock on <path>.lock and keeps it until close().

Fails closed: an unreadable ledger, a failed write or any internal error means reserve()
returns None, and a cost that could not be written stays counted in memory."""
import fcntl, json, logging, math, os, tempfile, threading, time, datetime as _dt

log = logging.getLogger("spend_cap")
_M = 1_000_000                                  # micro-dollars per dollar


class CapConfigError(Exception):
    pass


class SingleWriterError(CapConfigError):
    pass


class _LedgerCorrupt(Exception):
    pass


def _money(x):
    """A finite, non-negative real number (not bool) -> micro-dollars; anything else -> None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if not math.isfinite(x) or x < 0:
        return None
    return round(x * _M)


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


class SpendCap:
    def __init__(self, path, cap_usd, *, per_call_fallback_usd=0.02, calls_per_check=3, clock=time.time):
        cap, fb = _money(cap_usd), _money(per_call_fallback_usd)
        if cap is None:
            raise CapConfigError("cap_usd must be a finite number >= 0")
        if fb is None:
            raise CapConfigError("per_call_fallback_usd must be a finite number >= 0")
        if isinstance(calls_per_check, bool) or not isinstance(calls_per_check, int) or calls_per_check < 1:
            raise CapConfigError("calls_per_check must be an integer >= 1")
        self._path = os.path.abspath(os.fspath(path))
        self._cap, self._fallback, self._hold_size = cap, fb, fb * calls_per_check
        self._clock = clock
        self._lock = threading.Lock()
        self._held = 0                           # open holds, micro-dollars
        self._unwritten = 0                      # recorded today but not yet on disk
        self._unwritten_day = None
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

    # -- single-writer lock
    def close(self):
        with self._lock:
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
        return _dt.datetime.fromtimestamp(self._clock(), _dt.timezone.utc).date().isoformat()

    def _read_spent(self, today):
        try:
            with open(self._path, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError):
            raise _LedgerCorrupt from None
        if not isinstance(data, dict) or set(data) != {"day", "spent_usd"} or not isinstance(data["day"], str):
            raise _LedgerCorrupt
        spent = _money(data["spent_usd"])
        try:
            day = _dt.date.fromisoformat(data["day"]).isoformat()
        except ValueError:
            raise _LedgerCorrupt from None
        if spent is None or day != data["day"] or day > today:   # a future day is not trusted
            raise _LedgerCorrupt
        return spent if day == today else 0

    def _write(self, today, spent):
        d = os.path.dirname(self._path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".spend_cap.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"day": today, "spent_usd": spent / _M}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _spent_today(self, today):
        if self._unwritten_day != today:
            self._unwritten, self._unwritten_day = 0, today
        return self._read_spent(today) + self._unwritten

    # -- public
    def reserve(self):
        try:
            with self._lock:
                today = self._today()
                try:
                    spent = self._spent_today(today)
                except _LedgerCorrupt:
                    log.error("spend_cap_error code=LEDGER_CORRUPT")
                    return None
                if spent + self._held + self._hold_size > self._cap:
                    return None
                self._held += self._hold_size
                return Reservation(self, self._hold_size)
        except Exception:
            log.error("spend_cap_error code=INTERNAL")
            return None

    def _record(self, res, amount):
        with self._lock:
            take = min(amount, res._left)
            res._left -= take
            self._held -= take
            today = self._today()
            try:
                spent = self._spent_today(today) - self._unwritten
            except _LedgerCorrupt:
                log.error("spend_cap_error code=LEDGER_CORRUPT")
                self._unwritten += amount        # keep it counted; reserve() refuses while corrupt anyway
                return
            try:
                self._write(today, spent + self._unwritten + amount)
                self._unwritten = 0
            except OSError:
                log.error("spend_cap_error code=LEDGER_WRITE")
                self._unwritten += amount

    def _release(self, res):
        with self._lock:
            self._held -= res._left
            res._left = 0


class Reservation:
    def __init__(self, cap, hold):
        self._cap, self._left, self._closed = cap, hold, False

    def record(self, cost_usd):
        """Add a real provider cost to today's total. Bad costs count as the fallback. Never raises."""
        amount = _money(cost_usd)
        if amount is None:
            amount = self._cap._fallback
        try:
            self._cap._record(self, amount)
        except Exception:
            log.error("spend_cap_error code=INTERNAL")

    def close(self):
        if not self._closed:
            self._closed = True
            self._cap._release(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
