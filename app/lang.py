"""Reply language: ru or en.

Resolution order, first hit wins:
  (a) a `/start` payload token ru|en in the current message (it also sets the choice);
  (b) the user's explicit choice (the Русский / English buttons, callback_data lang:ru / lang:en);
  (c) the Telegram profile language_code: CIS-region languages (DEFAULT_RU) and no code -> ru,
      anything else -> en.

The explicit choice lives in memory only (`Choices`): user id -> "ru"|"en", bounded
(oldest dropped), cleared for the user by /forget, lost on restart. Nothing is written to disk.
"""
from collections import OrderedDict

IMPLEMENTED = True

DEFAULT_RU = frozenset({"ru", "uk", "be", "kk", "uz", "ky", "tg", "hy", "az", "ka"})
LANGS = ("ru", "en")
CALLBACK_PREFIX = "lang:"
MAX_CHOICES = 10000


def payload_lang(start_text):
    """ru|en from the `/start` payload (split on "_"), else None. Never raises."""
    try:
        if isinstance(start_text, str) and start_text.lower().startswith("/start"):
            parts = start_text.split(maxsplit=1)
            if len(parts) > 1:
                for token in parts[1].split("_"):
                    if token.lower() in LANGS:
                        return token.lower()
    except Exception:
        pass
    return None


def profile_lang(language_code):
    """The default for a Telegram language_code: CIS languages and no code -> ru, else en."""
    try:
        if isinstance(language_code, str) and language_code.strip():
            primary = language_code.strip().lower().replace("_", "-").split("-", 1)[0]
            return "ru" if primary in DEFAULT_RU else "en"
    except Exception:
        pass
    return "ru"


def pick_lang(start_text, language_code, choice=None):
    """Payload token, then the user's explicit `choice` ("ru"|"en"), then the profile default."""
    return payload_lang(start_text) or (choice if choice in LANGS else None) or profile_lang(language_code)


def callback_data(code):
    return CALLBACK_PREFIX + code


def parse_callback(data):
    """"ru"|"en" from callback_data `lang:ru` / `lang:en`, else None."""
    if isinstance(data, str) and data.startswith(CALLBACK_PREFIX) and data[len(CALLBACK_PREFIX):] in LANGS:
        return data[len(CALLBACK_PREFIX):]
    return None


def keyboard():
    """Inline keyboard (Bot API reply_markup dict) with the two language buttons."""
    return {"inline_keyboard": [[{"text": "Русский", "callback_data": callback_data("ru")},
                                 {"text": "English", "callback_data": callback_data("en")}]]}


class Choices:
    """In-memory map user id -> "ru"|"en", at most `limit` entries (oldest dropped)."""

    def __init__(self, limit=MAX_CHOICES):
        self.limit = limit
        self._map = OrderedDict()

    def get(self, user):
        return self._map.get(user)

    def set(self, user, code):
        if code not in LANGS or user is None:
            return
        self._map.pop(user, None)
        self._map[user] = code
        while len(self._map) > self.limit:
            self._map.popitem(last=False)

    def forget(self, user):
        self._map.pop(user, None)

    def __len__(self):
        return len(self._map)
