"""D-12 free-check counter: keyed hash only, 90-day retention, fail closed on the salt."""
import datetime as dt
import json
import os
import pathlib
import stat
import tempfile
import unittest
import unittest.mock

from app import check_counter
from app.check_counter import CheckCounter, CounterConfigError, salt_from_env

SALT = b"unit-test-salt-not-a-secret-0123456789"
USER = 987654321
DAY0 = dt.datetime(2026, 10, 9, 12, tzinfo=dt.timezone.utc).timestamp()


def at_day(n):
    return lambda: DAY0 + n * 86400


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, check_counter.FILE_NAME)

    def tearDown(self):
        self.tmp.cleanup()

    def counter(self, day=0):
        return CheckCounter(self.path, SALT, clock=at_day(day))

    def test_free_checks_then_refused(self):
        self.assertEqual(check_counter.FREE_CHECKS, 50)   # owner request 14.10 (was 3)
        c = self.counter()
        for _ in range(check_counter.FREE_CHECKS):
            self.assertTrue(c.allows(USER))
            c.charge(USER)
        self.assertFalse(c.allows(USER))
        self.assertTrue(c.allows(USER + 1))

    def test_existing_row_keeps_its_count_after_the_raise(self):
        # A row written while the limit was 3 (used 3, refused then) loads unchanged: 47 left.
        old = CheckCounter(self.path, SALT, free=3, clock=at_day(0))
        for _ in range(3):
            old.charge(USER)
        self.assertFalse(old.allows(USER))
        before = pathlib.Path(self.path).read_bytes()
        c = self.counter()
        self.assertEqual(pathlib.Path(self.path).read_bytes(), before)
        self.assertEqual(c.used(USER), 3)
        for _ in range(check_counter.FREE_CHECKS - 3):
            self.assertTrue(c.allows(USER))
            c.charge(USER)
        self.assertFalse(c.allows(USER))

    def test_file_has_only_key_used_last_and_no_raw_id(self):
        c = self.counter()
        c.charge(USER)
        c.charge(USER)
        raw = pathlib.Path(self.path).read_bytes()
        self.assertNotIn(str(USER).encode(), raw)
        rows = json.loads(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"key", "used", "last"})
        self.assertRegex(rows[0]["key"], r"^[0-9a-f]{64}$")
        self.assertEqual((rows[0]["used"], rows[0]["last"]), (2, "2026-10-09"))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_counts_survive_restart(self):
        self.counter().charge(USER)
        self.assertEqual(self.counter().used(USER), 1)

    def test_key_is_hmac_sha256_fixed_vector(self):
        # echo -n 987654321 | openssl dgst -sha256 -hmac 'unit-test-salt-not-a-secret-0123456789'
        self.assertEqual(self.counter().key(987654321),
                         "ba618e716a58d17fa034326cb728834872b873ae3f0535500164376b0dfff9b6")

    def test_key_depends_on_the_salt(self):
        other = CheckCounter(os.path.join(self.tmp.name, "other.json"), SALT[::-1])
        self.assertNotEqual(self.counter().key(USER), other.key(USER))

    def test_day_91_purges_day_89_keeps_at_startup(self):
        self.counter().charge(USER)
        self.assertEqual(self.counter(day=89).used(USER), 1)
        self.assertEqual(self.counter(day=90).used(USER), 1)
        self.assertEqual(self.counter(day=91).used(USER), 0)
        self.assertEqual(json.loads(pathlib.Path(self.path).read_bytes()), [])

    def test_purge_once_a_day_while_running(self):
        now = [DAY0]
        c = CheckCounter(self.path, SALT, clock=lambda: now[0])
        c.charge(USER)
        now[0] = DAY0 + 89 * 86400
        self.assertEqual(c.maybe_purge(), 0)
        self.assertEqual(c.used(USER), 1)
        now[0] = DAY0 + 91 * 86400
        self.assertEqual(c.maybe_purge(), 1)
        self.assertEqual(c.used(USER), 0)

    def test_last_moves_with_each_counted_check(self):
        self.counter().charge(USER)
        self.counter(day=80).charge(USER)
        self.assertEqual(self.counter(day=160).used(USER), 2)

    def test_missing_or_short_salt_fails_closed_names_only(self):
        for env in ({}, {"CHECK_COUNTER_SALT": ""}, {"CHECK_COUNTER_SALT": "short-salt-value-31-chars-xxxxx"}):
            with self.subTest(env=env), self.assertLogs("check_counter", "WARNING") as logs:
                self.assertIsNone(salt_from_env(env))
            self.assertIn("CHECK_COUNTER_SALT", logs.output[0])
            self.assertNotIn("short-salt-value", repr(logs.output))
        self.assertEqual(salt_from_env({"CHECK_COUNTER_SALT": "x" * 32}), b"x" * 32)
        for salt in (None, b"", b"x" * 31):
            with self.assertRaises(CounterConfigError):
                CheckCounter(self.path, salt)

    def test_corrupt_file_fails_closed(self):
        for content in (b"{", b"{}", b'[{"key": "ab", "used": 1, "last": "2026-10-09"}]',
                        json.dumps([{"key": "a" * 64, "used": 1, "last": "2026-10-09", "user": 5}]).encode(),
                        json.dumps([{"key": "a" * 64, "used": -1, "last": "2026-10-09"}]).encode(),
                        json.dumps([{"key": "a" * 64, "used": 1, "last": "yesterday"}]).encode()):
            with self.subTest(content=content):
                with open(self.path, "wb") as f:
                    f.write(content)
                with self.assertRaises(CounterConfigError):
                    self.counter()

    def test_forget_deletes_the_row_at_once_and_is_idempotent(self):
        c = self.counter()
        c.charge(USER)
        c.charge(USER + 1)
        key = c.key(USER)
        c.forget(USER)
        rows = json.loads(pathlib.Path(self.path).read_bytes())
        self.assertEqual([r["key"] for r in rows], [c.key(USER + 1)])
        self.assertNotIn(key.encode(), pathlib.Path(self.path).read_bytes())
        self.assertEqual(c.used(USER), 0)
        self.assertEqual(self.counter().used(USER), 0)
        c.forget(USER)
        c.forget(USER + 2)
        self.assertEqual(self.counter().used(USER + 1), 1)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_failed_forget_write_raises_and_keeps_the_row(self):
        c = self.counter()
        c.charge(USER)
        before = pathlib.Path(self.path).read_bytes()
        with unittest.mock.patch.object(check_counter.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                c.forget(USER)
        self.assertEqual(c.used(USER), 1)
        self.assertEqual(pathlib.Path(self.path).read_bytes(), before)
        c.forget(USER)
        self.assertEqual(self.counter().used(USER), 0)

    def test_bad_user_ids_rejected(self):
        c = self.counter()
        for bad in (0, -1, True, "5", None):
            with self.assertRaises(ValueError):
                c.key(bad)


if __name__ == "__main__":
    unittest.main()
