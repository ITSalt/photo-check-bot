"""Free-check counter (D-12): 50 free checks per Telegram user. Standard library only.

The file in DATA_DIR is a JSON list of rows {"key", "used", "last"} and nothing else:
key  = HMAC-SHA256(CHECK_COUNTER_SALT, decimal user id), hex; the raw id, names,
       usernames, photos and verdicts are never written;
used = checks counted so far (only MATCH or MISMATCH count, D-11, decided by the caller);
last = UTC date (YYYY-MM-DD) of the last counted check.
Rows whose last counted check is more than RETENTION_DAYS old are purged at startup
and once per UTC day; forget(user_id) deletes a user's row at once (/forget).

Fails closed: a missing or short salt, or an unreadable file at startup, raises
CounterConfigError and the caller keeps the vision check off. The rows live in memory
after the load (one process owns the file, the spend cap lock enforces it); a failed
write is logged by code and retried on the next change."""
import datetime as _dt
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import threading
import time

log = logging.getLogger("check_counter")

FREE_CHECKS = 50
RETENTION_DAYS = 90
MIN_SALT_CHARS = 32
SALT_ENV = "CHECK_COUNTER_SALT"
FILE_NAME = "check_counter.json"
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {"key", "used", "last"}


class CounterConfigError(Exception):
    """Messages carry names and codes only, never values."""


def salt_from_env(environ):
    """The salt as bytes, or None (unset or shorter than MIN_SALT_CHARS); warns by name only."""
    raw = str(environ.get(SALT_ENV) or "").strip()
    if len(raw) < MIN_SALT_CHARS:
        log.warning("vision disabled, unset or too short: %s", SALT_ENV)
        return None
    return raw.encode("utf-8")


class CheckCounter:
    def __init__(self, path, salt, *, free=FREE_CHECKS, retention_days=RETENTION_DAYS, clock=time.time):
        if not isinstance(salt, (bytes, bytearray)) or len(salt) < MIN_SALT_CHARS:
            raise CounterConfigError("salt missing or too short")
        if isinstance(free, bool) or not isinstance(free, int) or free < 0:
            raise CounterConfigError("free must be an integer >= 0")
        self._path = os.path.abspath(os.fspath(path))
        self._salt, self._free, self._retention = bytes(salt), free, retention_days
        self._clock = clock
        self._lock = threading.Lock()
        self._rows = self._load()               # key -> {"used": int, "last": "YYYY-MM-DD"}
        self._purged_on = None
        self.maybe_purge()

    # -- storage
    def _load(self):
        try:
            with open(self._path, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise CounterConfigError("check counter file unreadable") from None
        if not isinstance(data, list):
            raise CounterConfigError("check counter file corrupt")
        rows = {}
        for row in data:
            if not isinstance(row, dict) or set(row) != _FIELDS:
                raise CounterConfigError("check counter file corrupt")
            key, used, last = row["key"], row["used"], row["last"]
            if not isinstance(key, str) or not _KEY_RE.match(key) or key in rows:
                raise CounterConfigError("check counter file corrupt")
            if isinstance(used, bool) or not isinstance(used, int) or used < 0:
                raise CounterConfigError("check counter file corrupt")
            try:
                if not isinstance(last, str) or _dt.date.fromisoformat(last).isoformat() != last:
                    raise ValueError
            except ValueError:
                raise CounterConfigError("check counter file corrupt") from None
            rows[key] = {"used": used, "last": last}
        return rows

    def _write(self):
        rows = [{"key": k, "used": v["used"], "last": v["last"]} for k, v in sorted(self._rows.items())]
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._path), prefix=".check_counter.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rows, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _save(self):
        try:
            self._write()
        except OSError:
            log.error("check_counter_error code=WRITE")

    # -- helpers
    def _today(self):
        return _dt.datetime.fromtimestamp(self._clock(), _dt.timezone.utc).date()

    def key(self, user_id):
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("BAD_USER")
        return hmac.new(self._salt, str(user_id).encode("ascii"), hashlib.sha256).hexdigest()

    # -- public
    def maybe_purge(self):
        """Purge once per UTC day (and at startup). Returns the number of rows removed."""
        with self._lock:
            today = self._today()
            if self._purged_on == today:
                return 0
            self._purged_on = today
            old = [k for k, v in self._rows.items()
                   if (today - _dt.date.fromisoformat(v["last"])).days > self._retention]
            for k in old:
                del self._rows[k]
            if old:
                self._save()
            return len(old)

    def used(self, user_id):
        with self._lock:
            row = self._rows.get(self.key(user_id))
            return row["used"] if row else 0

    def allows(self, user_id):
        return self.used(user_id) < self._free

    def charge(self, user_id):
        """Count one check (the caller calls this only for MATCH or MISMATCH)."""
        with self._lock:
            key = self.key(user_id)
            row = self._rows.get(key) or {"used": 0, "last": None}
            self._rows[key] = {"used": row["used"] + 1, "last": self._today().isoformat()}
            self._save()

    def forget(self, user_id):
        """Delete the user's row at once (/forget). Idempotent.

        The file is rewritten atomically before this returns; on a failed write the row
        stays (in memory and on disk) and OSError is raised, so the caller can say so.
        """
        with self._lock:
            key = self.key(user_id)
            row = self._rows.pop(key, None)
            if row is None:
                return
            try:
                self._write()
            except OSError:
                self._rows[key] = row
                raise
