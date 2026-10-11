"""Durable `DATA_DIR/codes.log`: anonymised check codes that survive a redeploy (WP-UX-05, D-9).

A `logging.Handler` attached at startup in app/bot.py. It keeps only two record kinds and
writes each as one line `<YYYY-MM-DD UTC> <message>` (date only, no time of day):

  check_codes v=<1|2|3> a=<STATUS> n=<N> codes=<compact>   (the format app/recompute.py reads)
  verdict_feedback a=<MATCH|MISMATCH|NEEDS_REVIEW> diff=<color|size|logo|model|other>

Every message is validated against its fixed format (check_codes also through the recompute
parser, so a forged line with a wrong alphabet or an inconsistent attribute list is dropped);
any other record is dropped. Nothing here carries a user, chat, file or marker id or model
text, and the file has no link to a user: /forget deliberately does not touch it.

File: created 0600, appended with O_APPEND, one line per record. A write failure logs
`codes_log_error code=WRITE` (again only after a later write succeeded) and never breaks the bot.
Retention: at startup and once per UTC day (`maybe_rotate`, from the polling loop) the file is
rewritten atomically (temp + fsync + replace, 0600) keeping lines dated within the last 90 days
and at most 5 MiB (the oldest lines go first). Single writer: one process, like the spend cap.

Read it with `python3 -m app.recompute /data/codes.log`.
"""
import datetime as _dt
import logging
import os
import re
import stat
import tempfile
import threading
import time

from app import recompute

FILE_NAME = "codes.log"
RETENTION_DAYS = 90
MAX_BYTES = 5 * 1024 * 1024
RETRY_S = 3600

log = logging.getLogger("codes_log")

_CHECK = re.compile(r"check_codes v=([123]) a=(?:MATCH|MISMATCH|NEEDS_REVIEW) n=([0-9]{1,2}) "
                    r"codes=([0-9A-Za-z,;]{1,160})")
_FEEDBACK = re.compile(r"verdict_feedback a=(?:MATCH|MISMATCH|NEEDS_REVIEW) "
                       r"diff=(?:color|size|logo|model|other)")
_STORED = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}) ")


def valid(message):
    """True when the message is exactly a check_codes or a verdict_feedback line."""
    if not isinstance(message, str):
        return False
    if message.startswith("verdict_feedback "):
        return _FEEDBACK.fullmatch(message) is not None
    if message.startswith("check_codes "):
        m = _CHECK.fullmatch(message)
        if not m:
            return False
        try:
            recompute.parse_attrs(m.group(1), m.group(2), m.group(3))
        except recompute.BadLine:
            return False
        return True
    return False


class CodesLogHandler(logging.Handler):
    def __init__(self, path, clock=None):
        super().__init__(level=logging.INFO)
        self.path = path
        self.clock = clock or time.time          # tests replace it with a fake clock
        self._lock = threading.RLock()
        self._write_failed = False
        self._rotated_on = None
        self._retry_at = 0.0

    def _today(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc).date()

    def emit(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return                  # a record with bad format args: not ours, not a write failure
        try:
            if not valid(message):
                return
            line = ("%s %s\n" % (self._today().isoformat(), message)).encode("ascii")
            with self._lock:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(fd, line)
                finally:
                    os.close(fd)
                self._write_failed = False
        except Exception:
            self._fail()

    def _fail(self):
        if self._write_failed:
            return
        self._write_failed = True
        try:
            log.error("codes_log_error code=WRITE")
        except Exception:
            pass

    # --- retention -------------------------------------------------------------------

    def rotate(self):
        """Rewrite the file without lines older than 90 days and within 5 MiB.

        Returns the number of lines removed; raises OSError when the file cannot be rewritten."""
        with self._lock:
            try:
                with open(self.path, "rb") as f:
                    raw = f.read()
            except FileNotFoundError:
                return 0
            cutoff = self._today() - _dt.timedelta(days=RETENTION_DAYS)
            lines = raw.decode("ascii", "replace").splitlines()
            kept = []
            for line in lines:
                m = _STORED.match(line)
                if not m or not valid(line[11:]):
                    continue
                try:
                    day = _dt.date.fromisoformat(m.group(1))
                except ValueError:
                    continue
                if day >= cutoff:
                    kept.append(line + "\n")
            total = sum(len(x) for x in kept)
            drop = 0
            while total > MAX_BYTES and drop < len(kept):
                total -= len(kept[drop])
                drop += 1
            kept = kept[drop:]
            data = "".join(kept).encode("ascii")
            if data == raw:
                return 0
            folder = os.path.dirname(self.path) or "."
            fd, tmp = tempfile.mkstemp(prefix=".codes.", suffix=".tmp", dir=folder)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return len(lines) - len(kept)

    def maybe_rotate(self):
        """Rotate once per UTC day; after a failure retry no sooner than RETRY_S. Never raises."""
        try:
            today, now = self._today(), self.clock()
            if self._rotated_on == today or now < self._retry_at:
                return
            try:
                self.rotate()
            except Exception:
                log.error("codes_log_error code=ROTATE")
                self._retry_at = now + RETRY_S
                return
            self._rotated_on, self._retry_at = today, 0.0
        except Exception:
            pass

    def startup(self):
        """At process start: tighten the file mode to 0600, drop stale temp files, then rotate. Never raises."""
        self._rotated_on, self._retry_at = None, 0.0
        try:
            if os.path.exists(self.path) and stat.S_IMODE(os.stat(self.path).st_mode) != 0o600:
                os.chmod(self.path, 0o600)
        except OSError:
            pass
        folder = os.path.dirname(self.path) or "."
        try:
            for name in os.listdir(folder):
                if name.startswith(".codes.") and name.endswith(".tmp"):
                    try:
                        os.unlink(os.path.join(folder, name))
                    except OSError:
                        pass
        except OSError:
            pass
        self.maybe_rotate()


def attach(environ=None, logger=None, clock=None):
    """Attach one handler to the root logger (replacing an earlier one); returns it."""
    env = os.environ if environ is None else environ
    logger = logging.getLogger() if logger is None else logger
    for old in [h for h in logger.handlers if isinstance(h, CodesLogHandler)]:
        logger.removeHandler(old)
    handler = CodesLogHandler(os.path.join(env.get("DATA_DIR") or "/data", FILE_NAME), clock)
    logger.addHandler(handler)
    return handler


def detach(handler, logger=None):
    (logging.getLogger() if logger is None else logger).removeHandler(handler)
