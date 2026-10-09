"""WP-BOT-09: /forget through app.bot.handle, vision on and off; corrupt waitlist keeps the bot up.

Fakes only (tests/test_bot_wiring.py): no network, no keys.
"""
import json
import logging
import os
import pathlib
from unittest.mock import patch

from app import check_counter, waitlist
from tests.test_bot_wiring import FakeCompare, SALT, USER, NAME, USERNAME, WiringBase, update

OTHER = USER + 1


class ForgetTests(WiringBase):
    def setUp(self):
        super().setUp()
        self.waitlist_path = self.bot.waitlist.PATH
        for name, value in (("_purged_on", None), ("_retry_at", 0.0)):
            patcher = patch.object(waitlist, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def waitlist_users(self):
        return json.loads(pathlib.Path(self.waitlist_path).read_bytes())["users"]

    def counter_rows(self):
        return json.loads(pathlib.Path(os.path.join(self.data_dir, check_counter.FILE_NAME)).read_bytes())

    def test_forget_with_vision_on_deletes_waitlist_and_counter_rows(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.fresh()
                checks = self.build(FakeCompare(self, {"status": "MATCH"}))
                for user in (USER, OTHER):
                    self.run_updates(checks, update(user, lang, text="/start campaign_x"))
                    self.pair(checks, lang, user=user)
                key = checks.gate.counter.key(USER)
                self.assertEqual(len(self.counter_rows()), 2)
                with self.assertLogs("bot", logging.INFO) as logs:
                    sent = self.run_updates(checks, update(USER, lang, text="/forget"))
                self.assertEqual(sent, [self.bot.TEXTS[lang]["forget_done"]])
                self.assertIn("INFO:bot:forget done", logs.output)
                self.assertEqual(set(self.waitlist_users()), {str(OTHER)})
                self.assertEqual([r["key"] for r in self.counter_rows()], [checks.gate.counter.key(OTHER)])
                self.assertNotIn(key, json.dumps(self.counter_rows()))
                self.assertEqual(self.used(checks), 0)
                self.assertEqual(self.used(checks, OTHER), 1)
                # repeated /forget is fine
                sent = self.run_updates(checks, update(USER, lang, text="/forget"), update(USER, lang, text="/forget@SomeBot"))
                self.assertEqual(sent, [self.bot.TEXTS[lang]["forget_done"]] * 2)
                self.assertEqual(set(self.waitlist_users()), {str(OTHER)})
                self.assertEqual(len(self.counter_rows()), 1)

    def test_forget_drops_a_half_pair(self):
        fake = FakeCompare(self, {"status": "MATCH"})
        checks = self.build(fake)
        self.run_updates(checks, update(photo="gen-file-id-1"))
        self.assertTrue(checks.pending)
        self.run_updates(checks, update(text="/forget"))
        self.assertEqual(checks.pending, {})
        self.run_updates(checks, update(photo="real-file-id-1"))
        self.assertEqual(fake.calls, [])   # the old half was gone; no check ran

    def test_forget_with_vision_off_deletes_waitlist_row(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.fresh()
                self.run_updates(None, update(USER, lang, text="/start"), update(OTHER, lang, text="/start"))
                sent = self.run_updates(None, update(USER, lang, text="/forget"), update(USER, lang, text="/forget"))
                self.assertEqual(sent, [self.bot.TEXTS[lang]["forget_done"]] * 2)
                self.assertEqual(set(self.waitlist_users()), {str(OTHER)})

    def test_forget_on_corrupt_waitlist_answers_failed_and_keeps_file(self):
        os.makedirs(os.path.dirname(self.waitlist_path), exist_ok=True)
        content = b'{"users": {"%d": "direct", ' % USER
        pathlib.Path(self.waitlist_path).write_bytes(content)
        checks = self.build(FakeCompare(self, {"status": "MATCH"}))
        self.pair(checks)
        for photo_checks in (None, checks):
            with self.subTest(vision=photo_checks is not None):
                with self.assertLogs(level=logging.INFO) as logs:
                    sent = self.run_updates(photo_checks, update(text="/forget"))
                self.assertEqual(sent, [self.bot.TEXTS["ru"]["forget_failed"]])
                self.assertIn("ERROR:bot:forget_error code=WAITLIST_CORRUPT", logs.output)
                self.assertNotIn("INFO:bot:forget done", logs.output)
                self.assertNotIn(str(USER), "\n".join(logs.output))
                self.assertEqual(pathlib.Path(self.waitlist_path).read_bytes(), content)
        self.assertEqual(self.used(checks), 0)   # the counter row is still deleted

    def test_forget_counter_write_failure_answers_failed(self):
        checks = self.build(FakeCompare(self, {"status": "MATCH"}))
        self.pair(checks)
        with patch.object(check_counter.os, "replace", side_effect=OSError("disk")), \
                self.assertLogs("bot", logging.INFO) as logs:
            sent = self.run_updates(checks, update(text="/forget"))
        self.assertEqual(sent, [self.bot.TEXTS["ru"]["forget_failed"]])
        self.assertIn("ERROR:bot:forget_error code=COUNTER", logs.output)
        self.assertEqual(self.used(checks), 1)

    def test_corrupt_waitlist_start_still_answers_and_file_is_untouched(self):
        os.makedirs(os.path.dirname(self.waitlist_path), exist_ok=True)
        content = b'{"users": {"1": "dir'
        pathlib.Path(self.waitlist_path).write_bytes(content)
        checks = self.build(FakeCompare(self, {"status": "MATCH"}))
        for photo_checks, expected in ((None, [self.bot.TEXTS["ru"]["welcome"]]),
                                       (checks, [self.bot.TEXTS["ru"]["welcome_checks"], self.bot.TEXTS["ru"]["privacy"],
                                                 self.bot.TEXTS["ru"]["ask_generated"]])):
            with self.subTest(vision=photo_checks is not None), self.assertLogs("waitlist", "ERROR") as logs:
                sent = self.run_updates(photo_checks, update(text="/start campaign_x"))
                self.assertEqual(sent, expected)
                self.assertEqual(logs.output, ["ERROR:waitlist:waitlist_error code=CORRUPT"])
        self.assertEqual(pathlib.Path(self.waitlist_path).read_bytes(), content)

    def test_main_migrates_at_startup_and_purges_from_the_loop(self):
        os.makedirs(os.path.dirname(self.waitlist_path), exist_ok=True)
        pathlib.Path(self.waitlist_path).write_text(json.dumps({"users": {str(USER): "direct"}}))
        os.chmod(self.waitlist_path, 0o644)
        replies = iter([[], KeyboardInterrupt()])
        def call(method, params=None, timeout=None):
            value = next(replies)
            if isinstance(value, BaseException):
                raise value
            return value
        with patch.object(self.bot, "call", side_effect=call), patch.object(self.bot.logging, "basicConfig"), \
                patch.object(waitlist, "maybe_purge", wraps=waitlist.maybe_purge) as purge:
            with self.assertRaises(KeyboardInterrupt):
                self.bot.main(photo_checks=None)
        self.assertEqual(set(self.waitlist_users()[str(USER)]), {"source", "joined_at"})
        self.assertEqual(os.stat(self.waitlist_path).st_mode & 0o777, 0o600)
        self.assertEqual(purge.call_count, 3)   # startup + one per loop pass

    def test_logs_carry_no_identifiers(self):
        checks = self.build(FakeCompare(self, {"status": "MATCH"}))
        with self.assertLogs(level=logging.DEBUG) as logs:
            self.run_updates(checks, update(text="/start campaign_x"))
            self.pair(checks)
            self.run_updates(checks, update(text="/forget"))
            self.run_updates(None, update(OTHER, text="/start"), update(OTHER, text="/forget"))
            logging.getLogger("probe").info("probe")
        text = "\n".join(logs.output)
        for needle in (str(USER), str(OTHER), NAME, USERNAME, SALT, checks.gate.counter.key(USER)):
            self.assertNotIn(needle, text)
        self.assertEqual(text.count("forget done"), 2)
