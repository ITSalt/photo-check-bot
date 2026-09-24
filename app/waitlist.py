"""Counts who pressed /start and where they came from (the /start payload).

Stores only Telegram user ids and a source tag, as README rule 4 allows.
The file lives on a Docker volume so counts survive redeploys.
"""
import json
import os
import re
import threading

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PATH = os.path.join(DATA_DIR, "waitlist.json")
SOURCE_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_lock = threading.Lock()


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"users": {}}


def record(user_id, text):
    """Remember the first source a user came from. Returns (source, is_new)."""
    parts = (text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    source = payload if SOURCE_RE.match(payload) else "direct"
    with _lock:
        data = _load()
        key = str(user_id)
        is_new = key not in data["users"]
        if is_new:
            data["users"][key] = source
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, PATH)
    return source, is_new


def counts():
    data = _load()
    out = {}
    for src in data["users"].values():
        out[src] = out.get(src, 0) + 1
    return out
