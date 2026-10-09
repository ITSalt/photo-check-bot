"""Waitlist: sources, retention (D-14), safe migration, corrupt files, /forget. Fake clock only."""
import datetime as dt
import json
import logging
import os
import pathlib
import stat
import tempfile
import unittest
from unittest.mock import patch

from app import waitlist

DAY0 = dt.datetime(2026, 10, 9, 12, tzinfo=dt.timezone.utc).timestamp()
IDS = (111111111, 222222222, 333333333)


def at_day(n):
    return lambda: DAY0 + n * 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "waitlist.json")
        self.now = [DAY0]
        for name, value in (("DATA_DIR", self.dir.name), ("PATH", self.path),
                            ("clock", lambda: self.now[0]), ("_purged_on", None), ("_retry_at", 0.0)):
            patcher = patch.object(waitlist, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.dir.cleanup()

    def day(self, n):
        self.now[0] = DAY0 + n * 86400

    def raw(self):
        return pathlib.Path(self.path).read_bytes()

    def users(self):
        return json.loads(self.raw())["users"]

    def mode(self):
        return stat.S_IMODE(os.stat(self.path).st_mode)

    def write(self, content, mode=0o644, mtime=None):
        pathlib.Path(self.path).write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
        os.chmod(self.path, mode)
        if mtime is not None:
            os.utime(self.path, (mtime, mtime))


class RecordTest(Base):
    def test_source_from_payload(self):
        self.assertEqual(waitlist.record(1, "/start landing_ru"), ("landing_ru", True))

    def test_no_payload_is_direct(self):
        self.assertEqual(waitlist.record(2, "/start"), ("direct", True))

    def test_bad_payload_is_direct(self):
        self.assertEqual(waitlist.record(3, "/start <script>"), ("direct", True))

    def test_first_source_wins_and_counts_unique_users(self):
        waitlist.record(4, "/start tg_ads_1")
        self.assertEqual(waitlist.record(4, "/start landing_en"), ("landing_en", False))
        waitlist.record(5, "/start tg_ads_1")
        self.assertEqual(waitlist.counts(), {"tg_ads_1": 2})

    def test_row_has_source_and_joined_at_utc_date(self):
        self.now[0] = dt.datetime(2026, 10, 9, 23, 59, tzinfo=dt.timezone.utc).timestamp()
        waitlist.record(7, "/start landing_ru")
        self.assertEqual(self.users(), {"7": {"source": "landing_ru", "joined_at": "2026-10-09"}})

    def test_every_write_is_0600_and_leaves_no_temp_file(self):
        waitlist.record(1, "/start a")
        self.assertEqual(self.mode(), 0o600)
        self.write(json.loads(self.raw()), mode=0o644)
        waitlist.record(2, "/start b")
        self.assertEqual(self.mode(), 0o600)
        waitlist.forget(1)
        self.assertEqual(self.mode(), 0o600)
        self.assertEqual(os.listdir(self.dir.name), ["waitlist.json"])

    def test_write_is_atomic_replace_after_fsync(self):
        calls = []
        real_fsync, real_replace = os.fsync, os.replace
        with patch.object(waitlist.os, "fsync", side_effect=lambda fd: (calls.append("fsync"), real_fsync(fd))), \
                patch.object(waitlist.os, "replace", side_effect=lambda a, b: (calls.append(("replace", os.path.dirname(a))), real_replace(a, b))):
            waitlist.record(1, "/start a")
        self.assertEqual(calls[:2], ["fsync", ("replace", self.dir.name)])

    def test_writes_keep_other_top_level_keys(self):
        self.write({"users": {"1": "a"}, "note": {"keep": [1, 2]}, "version": 3}, mtime=DAY0)
        waitlist.migrate()
        waitlist.record(2, "/start b")
        waitlist.forget(1)
        data = json.loads(self.raw())
        self.assertEqual({k: v for k, v in data.items() if k != "users"}, {"note": {"keep": [1, 2]}, "version": 3})
        self.assertEqual(data["users"], {"2": {"source": "b", "joined_at": "2026-10-09"}})

    def test_failed_write_keeps_old_file_and_logs_code(self):
        waitlist.record(1, "/start a")
        before = self.raw()
        with patch.object(waitlist.os, "replace", side_effect=OSError("disk full")), \
                self.assertLogs("waitlist", "ERROR") as logs:
            self.assertEqual(waitlist.record(2, "/start b"), ("b", False))
        self.assertEqual(self.raw(), before)
        self.assertEqual(logs.output, ["ERROR:waitlist:waitlist_error code=WRITE"])
        self.assertEqual(os.listdir(self.dir.name), ["waitlist.json"])


class CorruptTest(Base):
    """AC 3a: record() never writes after a failed read (the wipe bug on main)."""

    CASES = (b"", b"{", b'{"users": {"1": "a", "2"', b"\xff\xfe", b"[]", b'{"users": []}',
             b'{"nousers": {}}', b'{"users": {"1": 5}}', b'{"users": {"1": {"source": "a"}}}',
             b'{"users": {"1": {"source": "a", "joined_at": "yesterday"}}}',
             b'{"users": {"1": {"source": "a", "joined_at": "2026-10-09", "x": 1}}}')

    def test_record_never_writes_after_a_failed_read(self):
        for content in self.CASES:
            with self.subTest(content=content):
                self.write(content)
                with patch.object(waitlist, "_write", wraps=waitlist._write) as write, \
                        self.assertLogs("waitlist", "ERROR") as logs:
                    self.assertEqual(waitlist.record(42, "/start x"), ("x", False))
                write.assert_not_called()
                self.assertEqual(self.raw(), content)
                self.assertEqual(logs.output, ["ERROR:waitlist:waitlist_error code=CORRUPT"])

    def test_unreadable_file_is_not_overwritten(self):
        self.write({"users": {"1": "a"}})
        before = self.raw()
        with patch("builtins.open", side_effect=PermissionError("denied")), \
                self.assertLogs("waitlist", "ERROR"):
            self.assertEqual(waitlist.record(42, "/start x"), ("x", False))
            self.assertEqual(waitlist.counts(), {})
        self.assertEqual(self.raw(), before)

    def test_counts_zero_totals_and_forget_raises_purge_and_migrate_leave_file(self):
        content = b'{"users": {"1": "a", "2"'
        self.write(content, mtime=DAY0 - 400 * 86400)
        with self.assertLogs("waitlist", "ERROR") as logs:
            self.assertEqual(waitlist.counts(), {})
            with self.assertRaises(waitlist.WaitlistError) as err:
                waitlist.forget(1)
            self.assertEqual(err.exception.code, "CORRUPT")
            self.assertFalse(waitlist.migrate())
            self.assertEqual(waitlist.maybe_purge(), 0)
            waitlist.startup()
        self.assertEqual(self.raw(), content)
        self.assertEqual(self.mode(), 0o644)   # left exactly as found, not even chmod
        for line in logs.output:
            self.assertIn("code=CORRUPT", line)
            self.assertNotIn('"1"', line)

    def test_failed_purge_retries_after_an_hour_not_every_poll(self):
        self.write(b"{")
        with self.assertLogs("waitlist", "ERROR") as logs:
            waitlist.maybe_purge()
            waitlist.maybe_purge()
            self.now[0] += 3601
            waitlist.maybe_purge()
        self.assertEqual(len(logs.output), 2)


class RetentionTest(Base):
    """AC 2: day 181 after join purges, day 179 keeps; PAID_LAUNCH_DATE + 31 days purges."""

    def setUp(self):
        super().setUp()
        waitlist.record(IDS[0], "/start a")

    def purge_on(self, n, env=None):
        self.day(n)
        return waitlist.maybe_purge(env or {})

    def test_day_179_keeps_day_181_purges(self):
        self.assertEqual(self.purge_on(179), 0)
        self.assertIn(str(IDS[0]), self.users())
        self.assertEqual(self.purge_on(181), 1)
        self.assertEqual(self.users(), {})

    def test_day_180_purges(self):
        # "deleted 180 days after joining": on day 180 the row goes.
        self.assertEqual(self.purge_on(180), 1)

    def test_purge_runs_once_per_utc_day(self):
        self.day(181)
        with patch.object(waitlist, "purge", wraps=waitlist.purge) as purge:
            waitlist.maybe_purge({})
            self.now[0] += 3600
            waitlist.maybe_purge({})
            self.now[0] += 86400
            waitlist.maybe_purge({})
        self.assertEqual(purge.call_count, 2)

    def test_startup_purges(self):
        self.day(200)
        waitlist.startup({})
        self.assertEqual(self.users(), {})

    def test_paid_launch_day_31_purges_day_29_keeps(self):
        self.day(10)
        waitlist.record(IDS[1], "/start b")
        env = {"PAID_LAUNCH_DATE": "2026-11-01"}       # DAY0 + 23
        self.assertEqual(self.purge_on(23 + 29, env), 0)
        self.assertEqual(len(self.users()), 2)
        self.assertEqual(self.purge_on(23 + 31, env), 2)
        self.assertEqual(self.users(), {})

    def test_whichever_first_join_limit_before_launch_limit(self):
        env = {"PAID_LAUNCH_DATE": "2027-12-01"}
        self.assertEqual(self.purge_on(181, env), 1)

    def test_future_launch_date_purges_nothing(self):
        self.assertEqual(self.purge_on(5, {"PAID_LAUNCH_DATE": "2026-10-20"}), 0)

    def test_invalid_or_unset_launch_is_not_launched_and_invalid_logged_by_code(self):
        for value in ("", "  ", None):
            with self.subTest(value=value):
                waitlist._purged_on = None
                env = {} if value is None else {"PAID_LAUNCH_DATE": value}
                self.assertEqual(self.purge_on(100, env), 0)
        for value in ("2026-13-01", "01.11.2026", "2026-11-1", "soon-SECRET"):
            with self.subTest(value=value):
                waitlist._purged_on = None
                with self.assertLogs("waitlist", "WARNING") as logs:
                    self.assertEqual(self.purge_on(100, {"PAID_LAUNCH_DATE": value}), 0)
                self.assertIn("WARNING:waitlist:waitlist_error code=BAD_PAID_LAUNCH_DATE", logs.output)
                self.assertNotIn(value, "\n".join(logs.output))
        self.assertIn(str(IDS[0]), self.users())

    def test_reads_env_by_default(self):
        with patch.dict(os.environ, {"PAID_LAUNCH_DATE": "2026-10-01"}):
            self.day(31)
            self.assertEqual(waitlist.maybe_purge(), 1)


class MigrationTest(Base):
    """AC 3: old format -> same ids and sources, joined_at = mtime date, 0600, idempotent."""

    OLD = {"users": {str(IDS[0]): "landing_ru", str(IDS[1]): "direct", str(IDS[2]): "tg_ads_1"}}
    MTIME = dt.datetime(2026, 9, 1, 23, 30, tzinfo=dt.timezone.utc).timestamp()

    def test_old_format_three_users_mixed_sources(self):
        self.write(self.OLD, mtime=self.MTIME)
        waitlist.startup({})
        users = self.users()
        self.assertEqual(list(users), list(self.OLD["users"]))
        self.assertEqual({k: v["source"] for k, v in users.items()}, self.OLD["users"])
        self.assertEqual({v["joined_at"] for v in users.values()}, {"2026-09-01"})
        self.assertEqual({tuple(sorted(v)) for v in users.values()}, {("joined_at", "source")})
        self.assertEqual(self.mode(), 0o600)
        self.assertEqual(os.listdir(self.dir.name), ["waitlist.json"])   # no backup copy

    def test_second_start_changes_nothing(self):
        self.write(self.OLD, mtime=self.MTIME)
        waitlist.startup({})
        before, st = self.raw(), os.stat(self.path)
        with patch.object(waitlist, "_write") as write:
            waitlist.startup({})
        write.assert_not_called()
        self.assertEqual(self.raw(), before)
        self.assertEqual(os.stat(self.path).st_ino, st.st_ino)
        self.assertEqual(self.mode(), 0o600)

    def test_new_format_untouched_except_mode(self):
        content = json.dumps({"users": {"5": {"source": "a", "joined_at": "2026-10-01"}}}, indent=1).encode()
        self.write(content, mode=0o644)
        ino = os.stat(self.path).st_ino
        with patch.object(waitlist, "_write") as write:
            waitlist.startup({})
        write.assert_not_called()
        self.assertEqual(self.raw(), content)
        self.assertEqual(os.stat(self.path).st_ino, ino)
        self.assertEqual(self.mode(), 0o600)

    def test_mixed_file_reads_both_formats(self):
        self.write({"users": {"1": "a", "2": {"source": "b", "joined_at": "2026-10-01"}}}, mtime=self.MTIME)
        self.assertEqual(waitlist.counts(), {"a": 1, "b": 1})
        self.assertEqual(waitlist.record(1, "/start z"), ("z", False))
        waitlist.record(3, "/start c")   # any write also upgrades old rows, with the mtime date
        self.assertEqual(self.users(), {"1": {"source": "a", "joined_at": "2026-09-01"},
                                        "2": {"source": "b", "joined_at": "2026-10-01"},
                                        "3": {"source": "c", "joined_at": "2026-10-09"}})

    def test_mtime_unavailable_means_today(self):
        self.write(self.OLD, mtime=self.MTIME)
        with patch.object(waitlist.os, "fstat", side_effect=OSError("no stat")):
            waitlist.migrate()
        self.assertEqual({v["joined_at"] for v in self.users().values()}, {"2026-10-09"})

    def test_old_mtime_rows_then_purged_by_retention(self):
        self.write(self.OLD, mtime=DAY0 - 181 * 86400)
        waitlist.startup({})
        self.assertEqual(self.users(), {})

    def test_startup_removes_stale_temp_files_only(self):
        self.write(self.OLD, mtime=self.MTIME)
        for name in ("waitlist.json.tmp", ".waitlist.abc123.tmp", "check_counter.json", "other.tmp", ".waitlist.keep"):
            pathlib.Path(self.dir.name, name).write_text('{"users": {"%d": "x"}}' % IDS[0])
        waitlist.startup({})
        self.assertEqual(sorted(os.listdir(self.dir.name)), [".waitlist.keep", "check_counter.json", "other.tmp", "waitlist.json"])
        self.assertEqual(set(self.users()), set(self.OLD["users"]))

    def test_no_file_is_fine(self):
        waitlist.startup({})
        self.assertFalse(os.path.exists(self.path))

    def test_failed_migration_write_keeps_old_file(self):
        self.write(self.OLD, mtime=self.MTIME)
        before = self.raw()
        with patch.object(waitlist.os, "replace", side_effect=OSError("disk")), \
                self.assertLogs("waitlist", "ERROR") as logs:
            self.assertFalse(waitlist.migrate())
        self.assertEqual(self.raw(), before)
        self.assertEqual(logs.output, ["ERROR:waitlist:waitlist_error code=WRITE"])


class ForgetTest(Base):
    def test_forget_deletes_row_and_is_idempotent(self):
        for uid in IDS:
            waitlist.record(uid, "/start a")
        waitlist.forget(IDS[1])
        self.assertEqual(set(self.users()), {str(IDS[0]), str(IDS[2])})
        self.assertNotIn(str(IDS[1]).encode(), self.raw())
        waitlist.forget(IDS[1])
        waitlist.forget(999)
        self.assertEqual(set(self.users()), {str(IDS[0]), str(IDS[2])})

    def test_forget_without_file_creates_nothing(self):
        waitlist.forget(IDS[0])
        self.assertFalse(os.path.exists(self.path))

    def test_logs_carry_no_ids(self):
        with self.assertLogs("waitlist", logging.DEBUG) as logs:
            for uid in IDS:
                waitlist.record(uid, "/start a")
            waitlist.forget(IDS[0])
            self.day(200)
            waitlist.maybe_purge({"PAID_LAUNCH_DATE": "bad"})
            self.write(b"{")
            waitlist.record(IDS[0], "/start a")
        text = "\n".join(logs.output)
        for uid in IDS:
            self.assertNotIn(str(uid), text)


if __name__ == "__main__":
    unittest.main()
