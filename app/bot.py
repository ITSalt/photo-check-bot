"""Telegram bot: long polling, standard library only.

`python -m app.bot` turns the photo check on only when app/vision_wiring.py finds
everything configured (VISION_*, CHECK_COUNTER_SALT, counter and spend cap);
otherwise the bot replies exactly as before (welcome, waitlist, fallback).
/forget works either way: it deletes the user's waitlist row and their free-check counter
row (app/waitlist.py, app/check_counter.py; with the photo check off, through a standalone
counter when CHECK_COUNTER_SALT is valid and the counter file exists). The waitlist is
migrated and purged at startup and purged once a UTC day from the polling loop.
"""
import json
import logging
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from app import lang, waitlist
from app.intake.parse import parse_update

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
POLL_TIMEOUT = 30
FILE_TIMEOUT = 30
CHUNK = 64 * 1024

# Bot replies and profile texts, RU and EN (rules: tests/test_bot_texts.py).
TEXTS = json.loads((pathlib.Path(__file__).parent / "bot_texts.json").read_text(encoding="utf-8"))

log = logging.getLogger("bot")


def call(method, params=None, timeout=POLL_TIMEOUT + 10):
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=timeout) as resp:
        body = json.load(resp)
    if not body.get("ok"):
        raise RuntimeError(f"{method}: {body.get('description')}")
    return body["result"]


def get_file(file_id):
    """Bot API getFile result ({"file_size", "file_path", ...}) for the session factory."""
    return call("getFile", {"file_id": file_id}, timeout=FILE_TIMEOUT)


def fetch_file(file_path):
    """Stream a file from the Bot API file endpoint in chunks. The URL holds the token: never logged."""
    if not isinstance(file_path, str) or not file_path or file_path.startswith("/") or ".." in file_path:
        raise ValueError("BAD_FILE_PATH")
    url = FILE_API + "/" + urllib.parse.quote(file_path, safe="/")
    with urllib.request.urlopen(url, timeout=FILE_TIMEOUT) as resp:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                return
            yield chunk


def reply_lang(msg):
    if not lang.IMPLEMENTED:  # task L10; Russian until it lands
        return "ru"
    try:
        return lang.pick_lang(msg.get("text"), (msg.get("from") or {}).get("language_code"))
    except Exception:  # a language pick must never take the bot down
        log.exception("pick_lang failed")
        return "ru"


def is_command(text, name):
    first = (text or "").split(maxsplit=1)
    return bool(first) and first[0].split("@", 1)[0].lower() == "/" + name


def forget_counter_row(user, environ=None):
    """With the photo check off: delete the user's free-check counter row all the same.

    Needs a valid CHECK_COUNTER_SALT (without it the key cannot be computed) and an
    existing DATA_DIR/check_counter.json; a corrupt file raises CounterConfigError.
    """
    from app import check_counter
    environ = os.environ if environ is None else environ
    salt = str(environ.get(check_counter.SALT_ENV) or "").strip()
    if len(salt) < check_counter.MIN_SALT_CHARS:
        return
    path = os.path.join(environ.get("DATA_DIR") or "/data", check_counter.FILE_NAME)
    if not os.path.exists(path):
        return
    check_counter.CheckCounter(path, salt.encode("utf-8")).forget(user)


def forget(user, photo_checks=None):
    """Delete everything the bot keeps about `user`. True when all of it is gone.

    Each store is rewritten atomically; failures are logged by code only, never an id.
    """
    done = True
    try:
        waitlist.forget(user)
    except waitlist.WaitlistError as exc:
        log.error("forget_error code=WAITLIST_%s", exc.code)
        done = False
    except Exception:
        log.error("forget_error code=WAITLIST")
        done = False
    try:
        if photo_checks is not None:
            photo_checks.forget(user)
        else:
            forget_counter_row(user)
    except Exception:
        log.error("forget_error code=COUNTER")
        done = False
    if done:
        log.info("forget done")
    return done


def handle(update, *, photo_checks=None):
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if chat is None:
        return
    if photo_checks is not None:
        try:
            event = parse_update(update)
        except Exception:
            log.warning("photo update rejected")
            return
        if event is None:
            return
    texts = TEXTS.get(reply_lang(msg), TEXTS["ru"])
    if is_command(msg.get("text"), "forget"):
        user = (msg.get("from") or {}).get("id")
        done = user is not None and forget(user, photo_checks)
        call("sendMessage", {"chat_id": chat, "text": texts["forget_done" if done else "forget_failed"]})
        return
    if (msg.get("text") or "").startswith("/start"):
        user = (msg.get("from") or {}).get("id")
        if user is not None:
            source, is_new = waitlist.record(user, msg.get("text"))
            if is_new:
                log.info("new user from source=%s totals=%s", source, waitlist.counts())
        if photo_checks is None:
            call("sendMessage", {"chat_id": chat, "text": texts["welcome"]})
            return
    if photo_checks is not None:
        try:
            messages = photo_checks.handle(event, lang=reply_lang(msg))
        except Exception:
            # Includes cleanup failure: no verdict and no raw exception/photo data.
            log.warning("photo processing stopped")
            return
        for text in messages:
            call("sendMessage", {"chat_id": chat, "text": text})
    else:
        call("sendMessage", {"chat_id": chat, "text": texts["fallback"]})


def main(*, photo_checks=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    offset = None
    log.info("bot started")
    waitlist.startup()
    while True:
        waitlist.maybe_purge()
        try:
            params = {"timeout": POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset
            for update in call("getUpdates", params):
                offset = update["update_id"] + 1
                handle(update, photo_checks=photo_checks)
            if photo_checks is not None:
                photo_checks.tick(send=lambda chat, text: call("sendMessage", {"chat_id": chat, "text": text}))
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            # Never log the URL: it contains the token.
            log.warning("polling error: %s", type(exc).__name__)
            time.sleep(5)


def build(environ=os.environ):
    """PhotoChecks when the vision check is fully configured, else None (bot as before)."""
    from app.vision_wiring import build_photo_checks
    return build_photo_checks(environ, get_file, fetch_file)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main(photo_checks=build())
