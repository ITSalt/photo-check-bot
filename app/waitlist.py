"""Waitlist for paid checks: who pressed /start and where they came from (the /start payload).

It serves the public 50% offer to the first 100 users (issue #17), so it keeps the raw
Telegram user id (D-14), as README rule 4 allows. Standard library only.

File DATA_DIR/waitlist.json: {"users": {"<id>": {"source": "<source>", "joined_at": "YYYY-MM-DD"}}}
(joined_at = UTC date). Older files hold rows "<id>": "<source>"; both formats are read.

Retention (D-14): a row is deleted 180 days after joined_at, or 30 days after paid checks
launch (PAID_LAUNCH_DATE, ISO date; unset or invalid = not launched, invalid is logged by
code), whichever comes first. The purge runs at startup and once per UTC day.
/forget deletes a user's row at once (forget()).

Migration at startup (startup()): old rows become {"source", "joined_at"}; a missing
joined_at is the UTC date of the file's mtime (today if the mtime is unavailable). The
mtime is the file's latest write, never earlier than any join, so this never shortens
anyone's retention. Ids and sources are kept exactly; no backup copy is made (it would
survive /forget). Running it again changes nothing. The file mode is set to 0600.
Temp files left by an interrupted write are removed at startup for the same reason.

Safety: every write is atomic (temp file in the same dir, 0600, fsync, os.replace). A
corrupt or unreadable file is never overwritten: the call logs `waitlist_error
code=CORRUPT` (no content) and skips the write; the bot keeps answering. Logs carry no ids.
"""
import datetime as _dt
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time

log = logging.getLogger("waitlist")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PATH = os.path.join(DATA_DIR, "waitlist.json")
SOURCE_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
RETENTION_DAYS = 180
AFTER_LAUNCH_DAYS = 30
PAID_LAUNCH_ENV = "PAID_LAUNCH_DATE"
RETRY_S = 3600
FILE_MODE = 0o600
_ROW_KEYS = {"source", "joined_at"}

clock = time.time          # tests replace it with a fake clock
_lock = threading.Lock()
_purged_on = None          # UTC date of the last successful purge
_retry_at = 0.0            # after a failed purge: no new attempt before this clock value


class WaitlistError(Exception):
    """Carries a code only (CORRUPT, WRITE), never file content or ids."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _today():
    return _dt.datetime.fromtimestamp(clock(), _dt.timezone.utc).date()


def _iso_date(value):
    """A date from a strict YYYY-MM-DD string, else None."""
    if not isinstance(value, str):
        return None
    try:
        day = _dt.date.fromisoformat(value)
    except ValueError:
        return None
    return day if day.isoformat() == value else None


def _load():
    """(data, users) with every row as {"source", "joined_at"}, plus whether any row was old.

    A missing file is an empty waitlist. Anything unreadable or not in either format raises
    WaitlistError("CORRUPT"); the caller must then not write.
    """
    try:
        with open(PATH, "rb") as f:
            raw = f.read()
            try:
                mtime = os.fstat(f.fileno()).st_mtime
                legacy_day = _dt.datetime.fromtimestamp(mtime, _dt.timezone.utc).date().isoformat()
            except (OSError, ValueError, OverflowError):
                legacy_day = _today().isoformat()
    except FileNotFoundError:
        return {"users": {}}, {}, False
    except OSError:
        raise WaitlistError("CORRUPT") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise WaitlistError("CORRUPT") from None
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        raise WaitlistError("CORRUPT")
    users, legacy = {}, False
    for key, row in data["users"].items():
        if isinstance(row, str):
            users[key] = {"source": row, "joined_at": legacy_day}
            legacy = True
        elif (isinstance(row, dict) and set(row) == _ROW_KEYS and isinstance(row["source"], str)
              and _iso_date(row["joined_at"]) is not None):
            users[key] = {"source": row["source"], "joined_at": row["joined_at"]}
        else:
            raise WaitlistError("CORRUPT")
    return data, users, legacy


def _write(data, users):
    """Atomic 0600 write; other top-level keys of the file are kept as they were."""
    out = dict(data)
    out["users"] = users
    try:
        os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PATH) or ".", prefix=".waitlist.", suffix=".tmp")
    except OSError:
        raise WaitlistError("WRITE") from None
    try:
        os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PATH)
    except BaseException as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise WaitlistError("WRITE") from None
        raise
    _fsync_dir()


def _fsync_dir():
    try:
        dfd = os.open(os.path.dirname(PATH) or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def _error(exc):
    log.error("waitlist_error code=%s", exc.code)


def paid_launch(environ=None):
    """PAID_LAUNCH_DATE as a date, or None (unset or invalid; invalid is logged by code)."""
    raw = str((os.environ if environ is None else environ).get(PAID_LAUNCH_ENV) or "").strip()
    if not raw:
        return None
    day = _iso_date(raw)
    if day is None:
        log.warning("waitlist_error code=BAD_PAID_LAUNCH_DATE")
    return day


def _expired(row, today, launch):
    joined = _dt.date.fromisoformat(row["joined_at"])
    if (today - joined).days >= RETENTION_DAYS:
        return True
    return launch is not None and (today - launch).days >= AFTER_LAUNCH_DAYS


def record(user_id, text):
    """Remember the first source a user came from. Returns (source, is_new).

    is_new is False when nothing was stored (already listed, or the file is unreadable or
    the write failed; both are logged by code and the file is left as it was).
    """
    parts = (text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    source = payload if SOURCE_RE.match(payload) else "direct"
    with _lock:
        try:
            data, users, _ = _load()
            key = str(user_id)
            if key in users:
                return source, False
            users[key] = {"source": source, "joined_at": _today().isoformat()}
            _write(data, users)
        except WaitlistError as exc:
            _error(exc)
            return source, False
    return source, True


def counts():
    """{source: users}; empty (zero totals) when the file is unreadable."""
    with _lock:
        try:
            _, users, _ = _load()
        except WaitlistError as exc:
            _error(exc)
            return {}
    out = {}
    for row in users.values():
        out[row["source"]] = out.get(row["source"], 0) + 1
    return out


def forget(user_id):
    """Delete the user's row at once. Idempotent. Raises WaitlistError (CORRUPT, WRITE)."""
    with _lock:
        data, users, _ = _load()
        if users.pop(str(user_id), None) is not None:
            _write(data, users)


def migrate():
    """Rewrite old rows in the new format (idempotent) and set the file mode to 0600.

    Returns True when the file is fine (or absent) afterwards; a corrupt file is left as it is.
    """
    with _lock:
        try:
            data, users, legacy = _load()
            if legacy:
                _write(data, users)
            elif os.path.exists(PATH) and stat.S_IMODE(os.stat(PATH).st_mode) != FILE_MODE:
                os.chmod(PATH, FILE_MODE)
        except WaitlistError as exc:
            _error(exc)
            return False
        except OSError:
            log.error("waitlist_error code=CHMOD")
            return False
    return True


def purge(environ=None):
    """Delete expired rows now. Returns the number removed. Raises WaitlistError."""
    today, launch = _today(), paid_launch(environ)
    with _lock:
        data, users, _ = _load()
        old = [k for k, row in users.items() if _expired(row, today, launch)]
        for k in old:
            del users[k]
        if old:
            _write(data, users)
    log.info("waitlist_purge removed=%d", len(old))
    return len(old)


def maybe_purge(environ=None):
    """Purge once per UTC day; after a failure, retry no sooner than RETRY_S. Never raises."""
    global _purged_on, _retry_at
    today, now = _today(), clock()
    if _purged_on == today or now < _retry_at:
        return 0
    try:
        removed = purge(environ)
    except WaitlistError as exc:
        _error(exc)
        _retry_at = now + RETRY_S
        return 0
    _purged_on, _retry_at = today, 0.0
    return removed


def _remove_stale_temp_files():
    """Temp files left by an interrupted write (older code used PATH + ".tmp") hold ids that
    would survive /forget and the purge; the live file is never one of them."""
    folder = os.path.dirname(PATH) or "."
    try:
        names = os.listdir(folder)
    except OSError:
        return
    base = os.path.basename(PATH)
    for name in names:
        if name == base + ".tmp" or (name.startswith(".waitlist.") and name.endswith(".tmp")):
            try:
                os.unlink(os.path.join(folder, name))
            except OSError:
                log.error("waitlist_error code=STALE_TEMP")


def startup(environ=None):
    """At process start: drop stale temp files, migrate, then purge. Never raises."""
    global _purged_on, _retry_at
    _purged_on, _retry_at = None, 0.0
    with _lock:
        _remove_stale_temp_files()
    if migrate():
        maybe_purge(environ)
