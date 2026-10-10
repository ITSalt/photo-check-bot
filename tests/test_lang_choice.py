"""WP-UX-03 (D-6): reply language. CIS profile languages default to ru; an explicit
Русский / English choice (/start, /lang buttons) beats the profile; a /start payload beats both.
Fakes only: no network. Marker ids are used to prove no id lands in callback_data or logs.
"""
import json
import unittest
from unittest.mock import patch

from app import lang
from app.photo_checks import TEXTS
from tests.test_bot_wiring import FakeCompare, WiringBase, update
from tests.test_two_step_feedback import callback

MARKER = 424242424


class DefaultTest(unittest.TestCase):
    def test_cis_codes_default_to_ru(self):
        for code in ("ru", "ru-RU", "uk", "be", "kk", "kk-KZ", "uz", "ky", "tg", "hy", "az", "ka", "UK", "Ru-ru"):
            self.assertEqual(lang.pick_lang(None, code), "ru", code)

    def test_others_default_to_en(self):
        for code in ("en", "en-US", "de", "fr", "pt-BR", "zh-hans", "ja"):
            self.assertEqual(lang.pick_lang(None, code), "en", code)

    def test_nothing_known_is_ru(self):
        for code in (None, "", "  "):
            self.assertEqual(lang.pick_lang(None, code), "ru", repr(code))

    def test_order_payload_choice_profile(self):
        self.assertEqual(lang.pick_lang("/start", "en-US", "ru"), "ru")
        self.assertEqual(lang.pick_lang("/start", "ru", "en"), "en")
        self.assertEqual(lang.pick_lang("/start en", "ru", "ru"), "en")
        self.assertEqual(lang.pick_lang("/start ru", "en", "en"), "ru")
        self.assertEqual(lang.pick_lang("/start", "en", "xx"), "en")  # junk choice ignored

    def test_choices_bounded_oldest_dropped(self):
        c = lang.Choices(limit=3)
        for user in (1, 2, 3, 4):
            c.set(user, "en")
        self.assertEqual((len(c), c.get(1), c.get(4)), (3, None, "en"))
        c.set(2, "ru")  # refreshed, so 3 is now the oldest
        c.set(5, "ru")
        self.assertEqual((c.get(2), c.get(3)), ("ru", None))
        c.set(9, "de")
        c.set(None, "ru")
        self.assertEqual(len(c), 3)
        c.forget(2)
        self.assertIsNone(c.get(2))

    def test_callback_data_exact(self):
        rows = lang.keyboard()["inline_keyboard"]
        self.assertEqual([[(b["text"], b["callback_data"]) for b in row] for row in rows],
                         [[("Русский", "lang:ru"), ("English", "lang:en")]])
        self.assertEqual((lang.parse_callback("lang:ru"), lang.parse_callback("lang:en")), ("ru", "en"))
        for bad in (None, "", "lang:", "lang:de", "lang:ru:1", "fb:ab", 5, "LANG:ru"):
            self.assertIsNone(lang.parse_callback(bad), bad)


class FlowTest(WiringBase):
    def setUp(self):
        super().setUp()
        self.calls = []

    def go(self, checks, *updates):
        start = len(self.calls)
        with patch.object(self.bot, "call", side_effect=lambda m, p=None, **k: self.calls.append((m, p))):
            for u in updates:
                self.bot.handle(u, photo_checks=checks)
        return self.calls[start:]

    def texts(self, calls):
        return [p["text"] for m, p in calls if m == "sendMessage"]

    def checks(self):
        return self.build(FakeCompare(self, {"status": "MISMATCH"}))

    def test_cis_profile_gets_ru_and_english_profile_gets_en(self):
        checks = self.checks()
        self.assertEqual(self.texts(self.go(checks, update(1, "kk", photo="gen-file-id-1")))[0],
                         TEXTS["ru"]["ask_real"])
        self.assertEqual(self.texts(self.go(checks, update(2, "en-US", photo="gen-file-id-1")))[0],
                         TEXTS["en"]["ask_real"])

    def test_start_welcome_has_the_keyboard_and_order(self):
        for checks in (self.checks(), None):
            with self.subTest(vision=checks is not None):
                self.bot.CHOICES = lang.Choices()
                calls = self.go(checks, update(7, "en-US", text="/start"))
                sends = [p for m, p in calls if m == "sendMessage"]
                markup = json.loads(sends[0]["reply_markup"])
                self.assertEqual(markup, lang.keyboard())
                self.assertEqual(sends[0]["text"], TEXTS["en"]["welcome_checks"] if checks
                                 else self.bot.TEXTS["en"]["welcome"])
                for later in sends[1:]:
                    self.assertNotIn("reply_markup", later)
                if checks:
                    self.assertEqual([p["text"] for p in sends[1:]],
                                     [TEXTS["en"]["privacy"], TEXTS["en"]["ask_generated"]])

    def test_lang_command_shows_buttons_in_current_language(self):
        for checks in (self.checks(), None):
            for command in ("/lang", "/language", "/lang@somebot"):
                with self.subTest(vision=checks is not None, command=command):
                    calls = self.go(checks, update(7, "en-US", text=command), update(8, "uk", text=command))
                    self.assertEqual([c[0] for c in calls], ["sendMessage", "sendMessage"])
                    self.assertEqual(calls[0][1]["text"], self.bot.TEXTS["en"]["lang_prompt"])
                    self.assertEqual(calls[1][1]["text"], self.bot.TEXTS["ru"]["lang_prompt"])
                    for _, p in calls:
                        self.assertEqual(json.loads(p["reply_markup"]), lang.keyboard())

    def test_press_sets_choice_and_confirms_in_new_language(self):
        for checks in (self.checks(), None):
            with self.subTest(vision=checks is not None):
                self.bot.CHOICES = lang.Choices()
                calls = self.go(checks, callback(MARKER, "lang:ru", "en-US", cid="c1"))
                self.assertEqual(calls[0], ("answerCallbackQuery", {"callback_query_id": "c1"}))
                self.assertEqual(calls[1][1]["text"], self.bot.TEXTS["ru"]["lang_set"])
                self.assertEqual(len(calls), 2)
                self.assertEqual(self.bot.CHOICES.get(MARKER), "ru")
                calls = self.go(checks, callback(MARKER, "lang:en", "ru", cid="c2"))
                self.assertEqual(calls[1][1]["text"], self.bot.TEXTS["en"]["lang_set"])
                self.assertEqual(self.bot.CHOICES.get(MARKER), "en")

    def test_unknown_and_foreign_presses_are_answered_but_ignored(self):
        checks = self.checks()
        for upd in (callback(MARKER, "lang:de"), callback(MARKER, "lang:"),
                    callback(MARKER, "lang:ru", chat_type="group", chat=-5),
                    callback(MARKER, "lang:ru", chat_type="channel", chat=-6)):
            for pc in (checks, None):
                with self.subTest(update=upd, vision=pc is not None):
                    calls = self.go(pc, upd)
                    self.assertEqual(calls[0], ("answerCallbackQuery", {"callback_query_id": "cbid-1"}))
                    self.assertIsNone(self.bot.CHOICES.get(MARKER))
                    self.assertEqual([c[0] for c in calls], ["answerCallbackQuery"])

    def test_choice_beats_profile_for_prompt_verdict_and_feedback(self):
        checks = self.checks()
        self.go(checks, callback(MARKER, "lang:ru", "en-US"))
        calls = self.go(checks, update(MARKER, "en-US", photo="gen-file-id-1"),
                        update(MARKER, "en-US", photo="real-file-id-1"))
        sent = self.texts(calls)
        self.assertEqual(sent[0], TEXTS["ru"]["ask_real"])
        self.assertTrue(sent[1].startswith(TEXTS["ru"]["MISMATCH"]), sent[1])
        data = json.loads(calls[-1][1]["reply_markup"])["inline_keyboard"][0][0]["callback_data"]
        calls = self.go(checks, callback(MARKER, data, "en-US"))
        self.assertEqual(self.texts(calls), [TEXTS["ru"]["feedback_ask"]])

    def test_choice_en_beats_cis_profile(self):
        checks = self.checks()
        self.go(checks, callback(MARKER, "lang:en", "ru"))
        self.assertEqual(self.texts(self.go(checks, update(MARKER, "ru", photo="gen-file-id-1")))[0],
                         TEXTS["en"]["ask_real"])

    def test_choice_is_per_user(self):
        checks = self.checks()
        self.go(checks, callback(MARKER, "lang:ru", "en-US"))
        self.assertEqual(self.texts(self.go(checks, update(MARKER + 1, "en-US", photo="gen-file-id-1")))[0],
                         TEXTS["en"]["ask_real"])

    def test_start_payload_beats_earlier_choice_and_sets_it(self):
        checks = self.checks()
        self.go(checks, callback(MARKER, "lang:ru", "en-US"))
        calls = self.go(checks, update(MARKER, "ru", text="/start landing_en"))
        self.assertEqual(self.texts(calls)[0], TEXTS["en"]["welcome_checks"])
        self.assertEqual(self.bot.CHOICES.get(MARKER), "en")
        calls = self.go(checks, update(MARKER, "ru", photo="gen-file-id-1"))
        self.assertEqual(self.texts(calls)[0], TEXTS["en"]["ask_real"])

    def test_forget_resets_to_default(self):
        for checks in (self.checks(), None):
            with self.subTest(vision=checks is not None):
                self.go(checks, callback(MARKER, "lang:ru", "en-US"))
                self.assertEqual(self.bot.CHOICES.get(MARKER), "ru")
                calls = self.go(checks, update(MARKER, "en-US", text="/forget"))
                self.assertEqual(self.texts(calls), [self.bot.TEXTS["ru"]["forget_done"]])  # said in the old choice
                self.assertIsNone(self.bot.CHOICES.get(MARKER))
                calls = self.go(checks, update(MARKER, "en-US", text="/lang"))
                self.assertEqual(self.texts(calls), [self.bot.TEXTS["en"]["lang_prompt"]])

    def test_restart_means_default(self):
        checks = self.checks()
        self.go(checks, callback(MARKER, "lang:ru", "en-US"))
        self.bot.CHOICES = lang.Choices()  # a restart: the map is gone
        self.assertEqual(self.texts(self.go(checks, update(MARKER, "en-US", photo="gen-file-id-1")))[0],
                         TEXTS["en"]["ask_real"])

    def test_no_ids_in_callback_data_and_logs(self):
        checks = self.checks()
        with self.assertLogs(level="DEBUG") as logs:  # /forget logs "forget done", so this is never empty
            calls = self.go(checks, update(MARKER, "en-US", text="/start"),
                            callback(MARKER, "lang:ru", "en-US"), update(MARKER, "en-US", text="/lang"),
                            update(MARKER, "en-US", text="/forget"))
        blob = json.dumps([p["reply_markup"] for _, p in calls if "reply_markup" in p])
        self.assertNotIn(str(MARKER), blob)
        self.assertEqual(blob.count("lang:ru"), 2)
        self.assertNotIn(str(MARKER), "\n".join(logs.output))
        # Every callback is answered exactly once.
        self.assertEqual([c[0] for c in calls].count("answerCallbackQuery"), 1)

    def test_press_without_vision_and_a_failing_send_does_not_crash(self):
        def boom(method, params=None, **kw):
            if method == "sendMessage":
                raise RuntimeError("down")
        with patch.object(self.bot, "call", side_effect=boom):
            self.bot.handle(callback(MARKER, "lang:ru"), photo_checks=None)
        self.assertEqual(self.bot.CHOICES.get(MARKER), "ru")

    def test_answer_failure_still_sets_choice(self):
        def boom(method, params=None, **kw):
            if method == "answerCallbackQuery":
                raise RuntimeError("down")
            self.calls.append((method, params))
        with patch.object(self.bot, "call", side_effect=boom):
            self.bot.handle(callback(MARKER, "lang:ru"), photo_checks=None)
        self.assertEqual(self.bot.CHOICES.get(MARKER), "ru")


if __name__ == "__main__":
    unittest.main()
