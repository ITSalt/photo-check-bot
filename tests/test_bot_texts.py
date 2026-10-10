"""Rules for the bot's own texts (app/bot_texts.json): profile fields and replies, RU and EN.

Profile limits are Telegram's (setMyName 64, setMyShortDescription 120, setMyDescription 512).
"""
import json
import pathlib
import re
import unittest

PATH = pathlib.Path(__file__).resolve().parent.parent / "app" / "bot_texts.json"
LIMITS = {"name": 64, "short_description": 120, "description": 512, "welcome": 1000, "fallback": 300,
          # photo check replies (app/photo_checks.py, WP-BOT-05)
          "welcome_checks": 1000, "privacy": 600, "ask_generated": 300, "ask_real": 300, "expired": 300,
          "cancelled": 300, "unsupported": 300, "busy": 300, "MATCH": 300, "MISMATCH": 300,
          "NEEDS_REVIEW": 400, "UNAVAILABLE": 300, "limit_reached": 300, "free_used_up": 400,
          "uncharged_limit": 300,
          # image refused before any provider call (WP-BOT-12 4b)
          "image_too_large": 300, "image_unreadable": 300,
          # two-step flow (WP-UX-01)
          "ask_generated_after_real": 300, "reminder_real": 300, "reminder_generated": 300,
          # wrong-verdict feedback (WP-UX-02)
          "feedback_button": 64, "feedback_ask": 200, "feedback_thanks": 300, "feedback_refused": 200,
          "code_color": 32, "code_size": 32, "code_logo": 32, "code_model": 32, "code_other": 32,
          # /forget replies (app/bot.py, WP-BOT-09)
          "forget_done": 300, "forget_failed": 300}
REQUIRED = set(LIMITS) - {"short_description", "description"}


class BotTextsTest(unittest.TestCase):
    def setUp(self):
        self.t = json.loads(PATH.read_text(encoding="utf-8"))

    def test_languages_and_keys(self):
        self.assertEqual(set(self.t), {"ru", "en"})
        for lang, texts in self.t.items():
            self.assertEqual(set(texts), set(LIMITS), lang)

    def test_limits_and_plain_text(self):
        for lang, texts in self.t.items():
            for key, value in texts.items():
                self.assertIsInstance(value, str, f"{lang}.{key}")
                self.assertLessEqual(len(value), LIMITS[key], f"{lang}.{key} is {len(value)} chars")
                if key in REQUIRED:
                    self.assertTrue(value.strip(), f"{lang}.{key} must not be empty")
                self.assertEqual(value, value.strip(), f"{lang}.{key} has leading/trailing whitespace")
                self.assertFalse(re.search(r"[<>]", value), f"{lang}.{key}: plain text, no HTML")
                self.assertNotRegex(value.lower(), r"https?://|www\.|javascript:|@\w{5,}bot\b",
                                    f"{lang}.{key}: no links or bot mentions")
            self.assertNotIn("\n", texts["name"] + texts["short_description"], f"{lang}: name and short description are one line")

    def test_two_step_wording(self):
        for lang, texts in self.t.items():
            for key in ("ask_generated", "ask_real", "ask_generated_after_real"):
                self.assertIn("1", texts[key], f"{lang}.{key}")
                self.assertIn("/privacy", texts[key], f"{lang}.{key}")
            for key in ("ask_real", "ask_generated_after_real", "reminder_real", "reminder_generated"):
                self.assertIn("2", texts[key], f"{lang}.{key}")
            for key in ("reminder_real", "reminder_generated"):
                self.assertIn("{minutes}", texts[key], f"{lang}.{key}")


if __name__ == "__main__":
    unittest.main()
