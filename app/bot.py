"""Minimal Telegram bot skeleton: long polling, standard library only.

Placeholder so the deploy pipeline can be tested end to end. The real
intake (paired photos) and checking logic come in through pull requests.
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

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
POLL_TIMEOUT = 30

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


def reply_lang(msg):
    if not lang.IMPLEMENTED:  # task L10; Russian until it lands
        return "ru"
    try:
        return lang.pick_lang(msg.get("text"), (msg.get("from") or {}).get("language_code"))
    except Exception:  # a language pick must never take the bot down
        log.exception("pick_lang failed")
        return "ru"


def handle(update):
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if chat is None:
        return
    texts = TEXTS.get(reply_lang(msg), TEXTS["ru"])
    if (msg.get("text") or "").startswith("/start"):
        user = (msg.get("from") or {}).get("id")
        if user is not None:
            source, is_new = waitlist.record(user, msg.get("text"))
            if is_new:
                log.info("new user from source=%s totals=%s", source, waitlist.counts())
        call("sendMessage", {"chat_id": chat, "text": texts["welcome"]})
    else:
        call("sendMessage", {"chat_id": chat, "text": texts["fallback"]})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    offset = None
    log.info("bot started")
    while True:
        try:
            params = {"timeout": POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset
            for update in call("getUpdates", params):
                offset = update["update_id"] + 1
                handle(update)
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            # Never log the URL: it contains the token.
            log.warning("polling error: %s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
