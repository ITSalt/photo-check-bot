"""Minimal Telegram bot skeleton: long polling, standard library only.

Placeholder so the deploy pipeline can be tested end to end. The real
intake (paired photos) and checking logic come in through pull requests.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
POLL_TIMEOUT = 30

WELCOME = (
    "Привет! Я проверяю ИИ-фото товара перед публикацией на маркетплейсе.\n"
    "Скоро здесь можно будет прислать сгенерированное фото и фото настоящей вещи, "
    "а я скажу, что исказилось и как это исправить.\n\n"
    "Присланные фото удаляются сразу после проверки."
)

log = logging.getLogger("bot")


def call(method, params=None, timeout=POLL_TIMEOUT + 10):
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=timeout) as resp:
        body = json.load(resp)
    if not body.get("ok"):
        raise RuntimeError(f"{method}: {body.get('description')}")
    return body["result"]


def handle(update):
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if chat is None:
        return
    if (msg.get("text") or "").startswith("/start"):
        call("sendMessage", {"chat_id": chat, "text": WELCOME})
    else:
        call("sendMessage", {"chat_id": chat, "text": "Проверка фото пока в разработке. Напишите /start."})


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
