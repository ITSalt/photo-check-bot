"""Contract for L10: app/lang.py pick_lang(start_text, language_code) -> "ru" | "en".

Rules:
1. A language tag in the /start payload wins. The payload is split on "_"; the first part that
   equals "ru" or "en" (case-insensitive) decides. "landing_en__studio" -> en; "tg_ruslan" has no tag.
2. Otherwise Telegram's language_code decides: it starts with "ru" -> ru; any other value -> en.
3. Nothing to go on (no tag, no language_code) -> ru: the bot speaks Russian first.
Never raises, whatever the input.
"""
import unittest

from app import lang


@unittest.skipUnless(lang.IMPLEMENTED, "L10 not implemented yet")
class PickLangTest(unittest.TestCase):
    def check(self, start_text, language_code, expected):
        self.assertEqual(lang.pick_lang(start_text, language_code), expected, (start_text, language_code))

    def test_payload_tag_wins_over_language_code(self):
        self.check("/start landing_en", "ru", "en")
        self.check("/start landing_ru", "en-GB", "ru")

    def test_segment_suffix_keeps_the_tag(self):
        self.check("/start landing_en__studio", None, "en")
        self.check("/start landing_ru__notyet", "en", "ru")

    def test_tag_is_case_insensitive(self):
        self.check("/start LANDING_EN", "ru", "en")

    def test_first_tag_decides(self):
        self.check("/start x_en_ru", "ru", "en")

    def test_tag_must_be_a_whole_part(self):
        self.check("/start tg_ruslan", "en", "en")
        self.check("/start tg_ruslan", None, "ru")
        self.check("/start open_ended", "ru", "ru")

    def test_no_tag_falls_back_to_language_code(self):
        self.check("/start tg_group1", "ru", "ru")
        self.check("/start tg_group1", "en-GB", "en")
        self.check("/start", "de", "en")
        self.check("/start", "ru-RU", "ru")
        self.check("hello", "en", "en")

    def test_payload_only_counts_after_start(self):
        self.check("landing_en", "ru", "ru")
        self.check("/help landing_en", "ru", "ru")

    def test_bot_mention_form(self):
        self.check("/start@contractsforpeoplebot landing_en", "ru", "en")

    def test_nothing_known_means_russian(self):
        self.check(None, None, "ru")
        self.check("", "", "ru")
        self.check("/start", None, "ru")

    def test_never_raises_and_returns_only_ru_or_en(self):
        for start_text in (None, "", "/start", "/start ", "/start  landing_en", "/start _", "/start __", 42, b"/start en", "/start " + "_" * 500):
            for code in (None, "", "EN", "ru", 7, "zz-ZZ"):
                self.assertIn(lang.pick_lang(start_text, code), ("ru", "en"), (start_text, code))


if __name__ == "__main__":
    unittest.main()
