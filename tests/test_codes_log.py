"""WP-UX-05: durable DATA_DIR/codes.log (D-9). Fake clock, fake transports, temp dirs only."""
import datetime as dt
import io
import logging
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app import codes_log, recompute, vision_compare
from app.photo_compare import run_bot_pair
from tests.test_t4_vision import ENV, FakeModel, Session
from tests.test_two_step_feedback import FeedbackBase, fb, token_of

CHECK = "check_codes v=2 a=NEEDS_REVIEW n=2 codes=1NPP011W,2MDD001C;n"
CHECK_OK = "check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n"
FEEDBACK = "verdict_feedback a=MISMATCH diff=color"
MARK = "ZQMARKERZQ"
NOW = 1_791_590_400.0 + 43200      # 2026-10-10 12:00:00 UTC
DAY = 86400


def day_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "codes.log")
        self.now = [NOW]
        self.logger = logging.Logger("codes-test")
        self.logger.setLevel(logging.INFO)
        self.handler = codes_log.attach({"DATA_DIR": self.dir}, logger=self.logger, clock=lambda: self.now[0])

    def read(self):
        with open(self.path, encoding="ascii") as f:
            return f.read()

    def write(self, text):
        with open(self.path, "w", encoding="ascii") as f:
            f.write(text)

    def stored(self, days_ago, message=CHECK_OK):
        return "%s %s\n" % (day_of(NOW - days_ago * DAY), message)


class WriteTests(Base):
    def test_codes_and_feedback_land_as_date_and_message(self):
        self.logger.info("%s", CHECK)
        self.logger.info(FEEDBACK)
        self.assertEqual(self.read(), "2026-10-10 %s\n2026-10-10 %s\n" % (CHECK, FEEDBACK))

    def test_date_only_no_time_of_day(self):
        self.now[0] = NOW + 3600 * 11 + 59 * 60
        self.logger.info(CHECK)
        self.assertEqual(self.read().split(" ")[0], "2026-10-10")
        self.assertRegex(self.read(), r"\A\d{4}-\d{2}-\d{2} check_codes ")

    def test_other_records_are_dropped(self):
        for message in ("vision_call ok n=2", "check_done a=MATCH", "check_codes_error code=RECORD",
                        "bot started", "polling error: URLError", "codes_log_error code=WRITE",
                        "see check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n",
                        "kind=same " + CHECK, "pair_reminder sent",
                        "verdict_feedback a=MATCH diff=color extra", "verdict_feedback a=MATCH diff=other\nx"):
            self.logger.info(message)
        self.assertFalse(os.path.exists(self.path))

    def test_marker_ids_never_reach_the_file(self):
        for message in ("check_done user=%s file=%s" % (MARK, MARK), "verdict_feedback a=MATCH diff=%s" % MARK,
                        "verdict_feedback a=%s diff=color" % MARK, "check_codes v=2 a=MATCH n=2 codes=%s" % MARK,
                        "check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n %s" % MARK,
                        "check_codes v=2 a=%s n=2 codes=1MDD011C,2MDD011C;n" % MARK,
                        "check_codes %s" % MARK, "verdict_feedback %s" % MARK):
            self.logger.info(message)
            self.logger.warning("%s", message)
        self.logger.info(CHECK_OK)
        self.assertNotIn(MARK, self.read())
        self.assertEqual(len(self.read().splitlines()), 1)

    def test_forged_codes_outside_the_alphabet_are_dropped(self):
        for forged in ("check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n!",
                       "check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n 123456789",
                       "check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n\nmore",
                       "check_codes v=2 a=MATCH n=2 codes=1MDD011C 2MDD011C;n",
                       "check_codes v=2 a=MATCH n=2 codes=é1MDD011C,2MDD011C;n",
                       "check_codes v=2 a=MATCH n=2 codes=" + "A" * 161,
                       "check_codes v=4 a=MATCH n=2 codes=1MDD011C,2MDD011C;n",
                       "check_codes v=2 a=NEEDS_REVIEW n=2 codes=1NNN000H,2MDD011C;n",   # H is a v=3 letter
                       "check_codes v=2 a=OTHER n=2 codes=1MDD011C,2MDD011C;n",
                       "check_codes v=2 a=MATCH n=3 codes=1MDD011C,2MDD011C;n",
                       "check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;z"):
            with self.assertNoLogs("codes_log"):          # dropped quietly: not a write failure
                self.logger.info("%s", forged)
        self.assertFalse(os.path.exists(self.path))

    def test_file_is_0600_even_under_a_loose_umask(self):
        old = os.umask(0)
        try:
            self.logger.info(CHECK)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_appends_to_existing_content(self):
        self.write(self.stored(3))
        self.logger.info(CHECK)
        self.assertEqual(self.read().splitlines()[0], self.stored(3).strip())
        self.assertEqual(len(self.read().splitlines()), 2)

    def test_write_failure_is_logged_once_and_never_raises(self):
        os.mkdir(self.path)            # a directory where the file should be: open fails
        errors = []
        with self.assertLogs("codes_log", "ERROR") as logs:
            for _ in range(3):
                self.logger.info(CHECK)
                self.logger.info(FEEDBACK)
        errors.extend(logs.output)
        self.assertEqual(errors, ["ERROR:codes_log:codes_log_error code=WRITE"])

    def test_write_flag_rearms_after_a_success(self):
        os.mkdir(self.path)
        with self.assertLogs("codes_log", "ERROR") as logs:
            self.logger.info(CHECK)
            self.logger.info(CHECK)                      # still failing: no second line
            os.rmdir(self.path)
            self.logger.info(CHECK)                      # success re-arms
            os.remove(self.path)
            os.mkdir(self.path)
            self.logger.info(CHECK)                      # fails again
        self.assertEqual(logs.output, ["ERROR:codes_log:codes_log_error code=WRITE"] * 2)

    def test_non_ascii_digits_are_dropped(self):
        for forged in ("check_codes v=2 a=MATCH n=\u0662 codes=1MDD011C,2MDD011C;n",
                       "check_codes v=\u0662 a=MATCH n=2 codes=1MDD011C,2MDD011C;n"):
            with self.assertNoLogs("codes_log"):
                self.logger.info("%s", forged)
        self.assertFalse(os.path.exists(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\u0662026-10-10 %s\n" % CHECK_OK)
        self.handler.startup()
        self.assertEqual(self.read(), "")

    def test_bad_format_args_are_skipped_silently(self):
        with self.assertNoLogs("codes_log"):
            self.logger.info("check_codes %s %s", "only-one")          # getMessage raises
            self.logger.info("verdict_feedback %d", "x")
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(self.handler._write_failed)
        self.logger.info(CHECK)                                          # still works afterwards
        self.assertEqual(len(self.read().splitlines()), 1)

    def test_startup_tightens_an_existing_loose_file(self):
        self.write(self.stored(1))
        os.chmod(self.path, 0o666)
        self.handler.startup()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_write_failure_does_not_break_a_check(self):
        os.mkdir(self.path)
        root = logging.getLogger()
        root.addHandler(self.handler)
        self.addCleanup(root.removeHandler, self.handler)
        old = root.level
        root.setLevel(logging.INFO)
        self.addCleanup(root.setLevel, old)
        compare = vision_compare.compare_from_env(ENV, transport=FakeModel())
        with patch("app.vision_compare.log.error"):        # the vision logger is not under test
            result = run_bot_pair(Session(), compare, None)
        self.assertEqual(result["status"], "MATCH")

    def test_unwritable_directory_does_not_raise(self):
        h = codes_log.CodesLogHandler(os.path.join(self.dir, "missing", "codes.log"), lambda: NOW)
        logger = logging.Logger("codes-test-2")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        logger.info(CHECK)
        h.startup()
        h.maybe_rotate()


class RetentionTests(Base):
    def test_91_days_removed_89_kept_at_startup(self):
        self.write(self.stored(91) + self.stored(89, CHECK) + self.stored(90) + self.stored(0, FEEDBACK))
        self.handler.startup()
        lines = self.read().splitlines()
        self.assertEqual(lines, [self.stored(89, CHECK).strip(), self.stored(90).strip(),
                                 self.stored(0, FEEDBACK).strip()])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_daily_tick_rotates_once_per_utc_day(self):
        self.write(self.stored(0))
        self.handler.startup()
        self.now[0] += 89 * DAY
        self.handler.maybe_rotate()
        self.assertEqual(len(self.read().splitlines()), 1)         # 89 days old: kept
        self.now[0] += 2 * DAY
        self.handler.maybe_rotate()
        self.assertEqual(self.read(), "")                            # 91 days old: removed

    def test_same_day_tick_does_not_rewrite(self):
        self.handler.startup()
        self.write(self.stored(200))
        self.handler.maybe_rotate()
        self.assertEqual(self.read(), self.stored(200))               # already rotated today

    def test_rotation_failure_retries_later_and_logs(self):
        os.mkdir(self.path)
        with self.assertLogs("codes_log", "ERROR") as logs:
            self.handler.maybe_rotate()
            self.handler.maybe_rotate()
        self.assertEqual(logs.output, ["ERROR:codes_log:codes_log_error code=ROTATE"])
        os.rmdir(self.path)
        self.write(self.stored(100))
        self.now[0] += 3601
        self.handler.maybe_rotate()
        self.assertEqual(self.read(), "")

    def test_unparsable_lines_and_foreign_content_are_dropped(self):
        self.write("garbage\n2026-13-45 %s\n%s 2026-10-10 %s\n%s ZQ %s\n"
                   % (CHECK_OK, day_of(NOW), CHECK_OK, day_of(NOW), MARK)
                   + "%s %s trailing\n" % (day_of(NOW), CHECK_OK))
        self.handler.startup()
        self.assertEqual(self.read(), "")

    def test_cap_drops_oldest_lines(self):
        line = "%s %s\n"
        old = line % (day_of(NOW - 10 * DAY), CHECK_OK)
        new = line % (day_of(NOW - 1 * DAY), CHECK)
        cap = len(old) + len(new) * 2
        with patch.object(codes_log, "MAX_BYTES", cap):
            self.write(old * 4 + new * 2)
            self.handler.startup()
        self.assertEqual(self.read(), old + new * 2)
        self.assertLessEqual(len(self.read().encode()), cap)

    def test_real_cap_is_5_mib(self):
        self.assertEqual(codes_log.MAX_BYTES, 5 * 1024 * 1024)
        line = self.stored(1)
        count = (codes_log.MAX_BYTES // len(line)) + 500
        self.write(line * count)
        self.handler.startup()
        self.assertLessEqual(os.path.getsize(self.path), codes_log.MAX_BYTES)
        self.assertGreater(os.path.getsize(self.path), codes_log.MAX_BYTES - len(line))

    def test_startup_removes_stale_temp_files(self):
        stale = os.path.join(self.dir, ".codes.abc123.tmp")
        other = os.path.join(self.dir, "waitlist.json")
        for p in (stale, other):
            open(p, "w").close()
        self.handler.startup()
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(other))

    def test_no_temp_file_left_after_rotation(self):
        self.write(self.stored(100) + self.stored(1))
        self.handler.startup()
        self.assertEqual(os.listdir(self.dir), ["codes.log"])


class RoundTripTests(Base):
    def test_recompute_reads_the_produced_file(self):
        root = logging.getLogger("vision")
        old_level, old_prop = root.level, root.propagate
        root.addHandler(self.handler)
        root.setLevel(logging.INFO)
        root.propagate = False
        self.addCleanup(lambda: (root.removeHandler(self.handler), root.setLevel(old_level),
                                 setattr(root, "propagate", old_prop)))
        for _ in range(2):
            compare = vision_compare.compare_from_env(ENV, transport=FakeModel())
            self.assertEqual(run_bot_pair(Session(), compare, None)["status"], "MATCH")
        self.logger.info(CHECK)
        self.logger.info(FEEDBACK)
        lines = self.read().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(sum(1 for x in lines if " check_codes v=3 " in x), 2)     # the two real checks
        self.assertEqual(sum(1 for x in lines if " check_codes v=2 " in x), 1)     # CHECK, an old line
        out = io.StringIO()
        with redirect_stdout(out):
            code = recompute._main([self.path])
        self.assertEqual(code, 0)
        self.assertIn("3", out.getvalue())
        self.assertNotIn("unparsable", out.getvalue())

    def test_recompute_reads_lines_after_rotation(self):
        self.write(self.stored(95) + self.stored(2, CHECK) + self.stored(1))
        self.handler.startup()
        self.assertEqual(len(list(recompute.read_lines(self.read().splitlines()))), 2)


class FeedbackIntegrationTests(FeedbackBase):
    def test_accepted_feedback_reaches_the_file_without_ids(self):
        with tempfile.TemporaryDirectory() as d:
            root = logging.getLogger()
            handler = codes_log.attach({"DATA_DIR": d}, clock=lambda: NOW)
            old = root.level
            root.setLevel(logging.INFO)
            self.addCleanup(root.setLevel, old)
            self.addCleanup(codes_log.detach, handler)
            self.status = "MISMATCH"
            token = token_of(self.verdict(user=777000111))
            self.checks.handle_feedback(fb(token, user=777000111), lang="ru")
            self.checks.handle_feedback(fb(token, "color", user=777000111), lang="ru")
            self.checks.handle_feedback(fb(token, "color", user=777000111), lang="ru")     # refused: no line
            with open(os.path.join(d, "codes.log"), encoding="ascii") as f:
                content = f.read()
        self.assertEqual(content, "2026-10-10 verdict_feedback a=MISMATCH diff=color\n")
        self.assertNotIn("777000111", content)
        self.assertNotIn(token, content)


class AttachTests(unittest.TestCase):
    def test_attach_replaces_and_detach_removes(self):
        logger = logging.Logger("attach-test")
        a = codes_log.attach({"DATA_DIR": "/nonexistent-x"}, logger=logger)
        b = codes_log.attach({"DATA_DIR": "/nonexistent-y"}, logger=logger)
        self.assertEqual(logger.handlers, [b])
        codes_log.detach(b, logger=logger)
        self.assertEqual(logger.handlers, [])
        self.assertEqual(a.path, "/nonexistent-x/codes.log")

    def test_default_data_dir(self):
        logger = logging.Logger("attach-test-2")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATA_DIR", None)
            self.assertEqual(codes_log.attach(logger=logger).path, "/data/codes.log")


if __name__ == "__main__":
    unittest.main()
