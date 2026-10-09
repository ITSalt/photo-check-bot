"""WP-BOT-10: per-user daily cap on uncharged checks (D-15). Fakes only: no network, no keys."""
import json
import logging
import os
import pathlib
from unittest.mock import patch

from app import check_counter, vision_wiring
from app.photo_checks import TEXTS
from tests.test_bot_wiring import FakeCompare, USER, WiringBase, update

DAY0 = 1_791_590_400.0 + 12 * 3600   # midnight UTC of the base day + 12 h (1_791_590_400 is 00:00 UTC)
NEEDS = {"status": "NEEDS_REVIEW", "total": 12, "confirmed": [], "to_check": [], "differs": [], "extra": "none"}


class UnchargedLimitTests(WiringBase):
    def setUp(self):
        super().setUp()
        self.now = [DAY0]

    def make(self, result, env_limit=None, **kw):
        env = dict(self.env)
        if env_limit is not None:
            env["UNCHARGED_DAILY_LIMIT"] = env_limit
        fake = FakeCompare(self, result, **kw)
        return fake, self.build(fake, env=env, clock=lambda: self.now[0])

    def counter_bytes(self):
        return pathlib.Path(os.path.join(self.data_dir, check_counter.FILE_NAME)).read_bytes()

    def test_eleventh_uncharged_check_is_refused_without_download_or_compare(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.fresh()
                fake, checks = self.make(NEEDS)
                for _ in range(10):
                    self.assertTrue(self.pair(checks, lang)[1].startswith(TEXTS[lang]["NEEDS_REVIEW"]))
                gets, calls = len(self.telegram.get_calls), len(fake.calls)
                reserve = []
                real = checks.gate.cap.reserve
                with patch.object(checks.gate.cap, "reserve", side_effect=lambda: reserve.append(1) or real()), \
                        self.assertLogs("vision_wiring", logging.INFO) as logs:
                    sent = self.pair(checks, lang)
                self.assertEqual(sent[1], TEXTS[lang]["uncharged_limit"])
                self.assertIn("INFO:vision_wiring:check_refused code=UNCHARGED_LIMIT", logs.output)
                self.assertEqual((len(self.telegram.get_calls), len(fake.calls)), (gets, calls))
                self.assertEqual(reserve, [])
                self.assertEqual(os.listdir(self.photo_dir), [])
                self.assertNotIn(str(USER), repr(logs.output))

    def test_unavailable_and_overrun_count_too(self):
        fake, checks = self.make({"status": "UNAVAILABLE"}, env_limit="2")
        self.pair(checks)
        self.pair(checks)
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["uncharged_limit"])

    def test_refused_for_any_check_for_the_rest_of_the_day_then_new_day_allows(self):
        fake, checks = self.make(NEEDS, env_limit="2")
        self.pair(checks)
        self.pair(checks)
        fake.result = {"status": "MATCH"}     # a would-be charged outcome is still refused today
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["uncharged_limit"])
        self.now[0] += 11 * 3600              # 23:00 UTC, same day
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["uncharged_limit"])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.used(checks), 0)
        self.now[0] += 2 * 3600               # 01:00 UTC next day
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["MATCH"]))
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(self.used(checks), 1)

    def test_other_users_are_not_affected(self):
        fake, checks = self.make(NEEDS, env_limit="1")
        self.pair(checks)
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["uncharged_limit"])
        self.assertTrue(self.pair(checks, user=USER + 1)[1].startswith(TEXTS["ru"]["NEEDS_REVIEW"]))

    def test_charged_checks_do_not_count(self):
        for result in ({"status": "MATCH"}, {"status": "MISMATCH", "total": 12, "confirmed": [], "to_check": [],
                                              "differs": [], "extra": "none"}):
            with self.subTest(status=result["status"]):
                self.fresh()
                fake, checks = self.make(result, env_limit="1")
                checks.gate.counter._free = 100
                for _ in range(5):
                    self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"][result["status"]]))
                self.assertEqual(len(fake.calls), 5)
                self.assertEqual(checks.gate._uncharged, {})
                fake.result = NEEDS               # the first uncharged one still fits
                self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["NEEDS_REVIEW"]))

    def test_nothing_on_disk_and_only_hmac_keys_in_memory(self):
        fake, checks = self.make(NEEDS)
        self.pair(checks)
        self.pair(checks, user=USER + 1)
        self.fresh_counter_file = None
        # a charged check creates the file; uncharged ones must leave it byte-identical
        fake.result = {"status": "MATCH"}
        self.pair(checks, user=USER + 2)
        before = self.counter_bytes()
        fake.result = NEEDS
        for _ in range(3):
            self.pair(checks)
        self.assertEqual(self.counter_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.data_dir)), sorted([check_counter.FILE_NAME, "spend_cap.json", "spend_cap.json.lock"]))
        keys = set(checks.gate._uncharged)
        self.assertEqual(keys, {checks.gate.counter.key(USER), checks.gate.counter.key(USER + 1)})
        self.assertTrue(all(len(k) == 64 and k not in (str(USER), str(USER + 1)) for k in keys))
        self.assertNotIn(str(USER).encode(), self.counter_bytes())

    def test_uncharged_only_user_leaves_no_counter_file(self):
        fake, checks = self.make(NEEDS)
        self.pair(checks)
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, check_counter.FILE_NAME)))

    def test_forget_clears_the_in_memory_count(self):
        fake, checks = self.make(NEEDS, env_limit="1")
        self.pair(checks)
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["uncharged_limit"])
        self.run_updates(checks, update(USER, "ru", text="/forget"))
        self.assertNotIn(checks.gate.counter.key(USER), checks.gate._uncharged)
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["NEEDS_REVIEW"]))

    def test_limit_from_env(self):
        for raw, want, warns in ((None, 10, False), ("", 10, False), ("  7 ", 7, False), ("1", 1, False),
                                 ("0", 10, True), ("-3", 10, True), ("abc", 10, True), ("2.5", 10, True)):
            with self.subTest(raw=raw):
                env = {} if raw is None else {"UNCHARGED_DAILY_LIMIT": raw}
                if warns:
                    with self.assertLogs("vision_wiring", logging.WARNING) as logs:
                        self.assertEqual(vision_wiring.uncharged_limit_from_env(env), want)
                    self.assertEqual(logs.output, ["WARNING:vision_wiring:uncharged_limit_config code=INVALID"])
                else:
                    self.assertEqual(vision_wiring.uncharged_limit_from_env(env), want)
        fake, checks = self.make(NEEDS)
        self.assertEqual(checks.gate.uncharged_limit, 10)

    def test_counts_reset_when_the_day_changes_in_memory(self):
        fake, checks = self.make(NEEDS, env_limit="1")
        self.pair(checks)
        self.now[0] += 86400
        self.pair(checks)
        self.assertEqual(len(checks.gate._uncharged), 1)
        self.assertEqual(len(fake.calls), 2)
