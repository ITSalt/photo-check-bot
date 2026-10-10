"""Telegram bot: long polling, standard library only.

`python -m app.bot` turns the photo check on only when app/vision_wiring.py finds
everything configured (VISION_*, CHECK_COUNTER_SALT, counter and spend cap);
otherwise the bot replies exactly as before (welcome, waitlist, fallback).
/forget works either way: it deletes the user's waitlist row and their free-check counter
row (app/waitlist.py, app/check_counter.py; with the photo check off, through a standalone
counter when CHECK_COUNTER_SALT is valid and the counter file exists). The waitlist is
migrated and purged at startup and purged once a UTC day from the polling loop.

Reply language (app/lang.py): a /start payload token, else the user's explicit choice from the
Русский / English buttons (/start, /lang), else the profile default (CIS languages -> ru). The
choice is kept in memory only (CHOICES), bounded, cleared by /forget and lost on restart.

DATA_DIR/codes.log (app/codes_log.py): check_codes and verdict_feedback lines only, no ids; kept 90 days.
"""
import json
import logging
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from app import codes_log, lang, waitlist
from app.intake.parse import parse_update
from app.photo_checks import Reply, parse_feedback_data

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
POLL_TIMEOUT = 30
FILE_TIMEOUT = 30
CHUNK = 64 * 1024
ALLOWED_UPDATES = ["message", "callback_query"]

# Bot replies and profile texts, RU and EN (rules: tests/test_bot_texts.py).
TEXTS = json.loads((pathlib.Path(__file__).parent / "bot_texts.json").read_text(encoding="utf-8"))

log = logging.getLogger("bot")

# Explicit language choices, user id -> "ru"|"en". Memory only: lost on restart, cleared by /forget.
CHOICES = lang.Choices()


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
        sender = msg.get("from") or {}
        user = sender.get("id") if type(sender.get("id")) is int else None
        picked = lang.pick_lang(msg.get("text"), sender.get("language_code"),
                                CHOICES.get(user) if user is not None else None)
        if user is not None and lang.payload_lang(msg.get("text")):
            CHOICES.set(user, picked)  # a /start payload also sets the choice
        return picked
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
    CHOICES.forget(user)
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


def parse_feedback(update):
    """Event {kind: "feedback", user_id, chat_id, token, code, callback_id} from a callback_query
    of a private chat, else None. Only ids Telegram sends; none goes back into callback_data."""
    query = update.get("callback_query")
    if not isinstance(query, dict):
        return None
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    parsed = parse_feedback_data(query.get("data"))
    if chat.get("type") != "private" or parsed is None:
        return None
    return {"kind": "feedback", "user_id": (query.get("from") or {}).get("id"), "chat_id": chat.get("id"),
            "token": parsed[0], "code": parsed[1], "callback_id": query.get("id")}


def send_reply(chat, text):
    params = {"chat_id": chat, "text": text}
    markup = getattr(text, "markup", None)
    if markup:
        params["reply_markup"] = json.dumps(markup)
    call("sendMessage", params)


def lang_markup(text):
    """The reply text with the Русский / English keyboard attached."""
    return Reply(text, lang.keyboard())


def handle_lang_callback(query, code):
    """A press on a language button: set the choice, confirm once in the new language."""
    chat = (query.get("message") or {}).get("chat") or {}
    user = (query.get("from") or {}).get("id")
    if chat.get("type") != "private" or type(user) is not int or type(chat.get("id")) is not int:
        return
    CHOICES.set(user, code)
    send_reply(chat["id"], TEXTS[code]["lang_set"])


def handle_callback(update, photo_checks):
    """Every callback_query is answered (no text); a language press sets the choice, a feedback
    press gets its reply."""
    query = update["callback_query"]
    if query.get("id") is not None:
        try:
            call("answerCallbackQuery", {"callback_query_id": query["id"]})
        except Exception as exc:
            log.warning("answerCallbackQuery error: %s", type(exc).__name__)
    code = lang.parse_callback(query.get("data"))
    if code is not None:
        try:
            handle_lang_callback(query, code)
        except Exception as exc:
            log.warning("language choice error: %s", type(exc).__name__)
        return
    if photo_checks is None:
        return
    event = parse_feedback(update)
    if event is None or type(event["chat_id"]) is not int:
        return
    try:
        replies = photo_checks.handle_feedback(event, lang=reply_lang({"from": query.get("from")}))
    except Exception:
        log.warning("feedback processing stopped")
        return
    for text in replies:
        send_reply(event["chat_id"], text)


def handle(update, *, photo_checks=None):
    if isinstance(update.get("callback_query"), dict):
        handle_callback(update, photo_checks)
        return
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if chat is None:
        return
    if is_command(msg.get("text"), "lang") or is_command(msg.get("text"), "language"):
        send_reply(chat, lang_markup(TEXTS.get(reply_lang(msg), TEXTS["ru"])["lang_prompt"]))
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
            send_reply(chat, lang_markup(texts["welcome"]))
            return
    if photo_checks is not None:
        try:
            messages = photo_checks.handle(event, lang=reply_lang(msg))
        except Exception:
            # Includes cleanup failure: no verdict and no raw exception/photo data.
            log.warning("photo processing stopped")
            return
        if messages and is_command(msg.get("text"), "start"):
            messages = [lang_markup(messages[0])] + list(messages[1:])  # keyboard on the welcome
        for text in messages:
            send_reply(chat, text)
    else:
        call("sendMessage", {"chat_id": chat, "text": texts["fallback"]})


def main(*, photo_checks=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    codes = codes_log.attach()          # DATA_DIR/codes.log: check_codes / verdict_feedback lines only
    try:
        codes.startup()
        _poll(photo_checks, codes)
    finally:
        codes_log.detach(codes)


def _poll(photo_checks, codes):
    offset = None
    log.info("bot started")
    waitlist.startup()
    while True:
        waitlist.maybe_purge()
        codes.maybe_rotate()
        try:
            params = {"timeout": POLL_TIMEOUT, "allowed_updates": json.dumps(ALLOWED_UPDATES)}
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
