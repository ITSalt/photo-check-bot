"""WP-BOT-05: the vision check wired into app/bot.py. Fakes only: no network, no keys.

Every scenario drives app.bot.handle with Telegram updates; getFile / file download,
sendMessage and the provider are fakes. Photos land in a temporary photo root that
must be empty after every check.
"""
import datetime
import importlib
import json
import logging
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from app import check_counter, lang, spend_cap, vision_compare, vision_wiring
from app.photo_checks import TEXTS
from tests.test_t4_vision import ENV as VISION_ENV, FakeModel, JPEG, KEY, PNG

SALT = "unit-test-salt-not-a-secret-0123456789"
USER, NAME, USERNAME = 987654321, "Alicefirstname", "alice_seller_nick"
FILES = {"gen-file-id-1": JPEG, "real-file-id-1": PNG}
MODEL_TEXT = "Same in both."
D11_RU = "Эта проверка не списана: бот не уверен — посмотрите отмеченные детали"
D11_EN = "This check was not counted: the bot is not sure — look at the marked details"
DETAIL = {"status": "NEEDS_REVIEW", "total": 12, "confirmed": [{"label": "x"}] * 9,
          "to_check": [{"label": "застёжка", "reason": "not_visible"}], "differs": [], "extra": "none"}

# Replies of the bot on main (392a8f7) with the feature off, copied verbatim.
MAIN_TEXTS = {
    "ru": {"welcome": "Привет! Я проверяю ИИ-фото товара перед публикацией на маркетплейсе.\nСкоро здесь можно будет прислать сгенерированное фото и фото настоящей вещи, а я скажу, что исказилось и как это исправить.\n\nПрисланные фото удаляются сразу после проверки.",
           "fallback": "Проверка фото пока в разработке. Напишите /start."},
    "en": {"welcome": "Hi! I check AI product photos before they go live on a marketplace.\nSoon you'll be able to send a generated photo and a photo of the real item here, and I'll tell you what changed and how to fix it.\n\nPhotos you send are deleted right after the check.",
           "fallback": "Photo checks are still in development. Send /start."},
}


def load_bot():
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "unit-test-placeholder"}):
        return importlib.import_module("app.bot")


def update(user=USER, lang="ru", text=None, photo=None, caption=None):
    msg = {"chat": {"id": user, "type": "private"},
           "from": {"id": user, "first_name": NAME, "username": USERNAME, "language_code": lang}}
    if text is not None:
        msg["text"] = text
    if photo is not None:
        msg["photo"] = [{"file_id": photo, "file_size": len(FILES.get(photo, b"x"))}]
        if caption:
            msg["caption"] = caption
    return {"update_id": 1, "message": msg}


class FakeTelegram:
    def __init__(self):
        self.get_calls, self.fetch_calls = [], []

    def get_file(self, file_id):
        self.get_calls.append(file_id)
        return {"file_id": file_id, "file_path": "photos/" + file_id, "file_size": len(FILES[file_id])}

    def fetch(self, file_path):
        self.fetch_calls.append(file_path)
        yield FILES[file_path.split("/", 1)[1]]


class FakeCompare:
    """compare_factory for build_photo_checks: three 'calls' per check, each marked with
    on_begin before it and reported to on_call after it (spend cap v2)."""
    def __init__(self, test, result, costs=(0.001, 0.002, 0.003), obey=True):
        self.test, self.result, self.costs, self.obey = test, result, costs, obey
        self.calls, self.on_call, self.provider_calls = [], None, 0
        self.on_begin = self.on_not_sent = None

    def __call__(self, environ, *, on_call=None, on_begin=None, on_not_sent=None):
        self.on_call, self.on_begin, self.on_not_sent = on_call, on_begin, on_not_sent
        return self.compare

    def compare(self, reference, candidate):
        # Roles: the real photo is the reference; both are on disk during the check.
        self.test.assertEqual((reference, candidate), (PNG, JPEG))
        self.test.assertEqual(len(os.listdir(self.test.photo_dir)), 1)
        self.calls.append(1)
        for cost in self.costs:
            if self.on_begin() is False:
                return {"status": "UNAVAILABLE"}
            self.provider_calls += 1
            if self.on_call(cost) is False and self.obey:
                return {"status": "UNAVAILABLE"}
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class WiringBase(unittest.TestCase):
    def setUp(self):
        self.bot = load_bot()
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.photo_dir = os.path.join(self.tmp.name, "photos")
        self.env = dict(VISION_ENV, CHECK_COUNTER_SALT=SALT)
        self.telegram = FakeTelegram()
        self.built = []
        patcher = patch.object(self.bot, "CHOICES", lang.Choices())  # no language choice leaks between tests
        patcher.start()
        self.addCleanup(patcher.stop)
        waitlist_path = os.path.join(self.tmp.name, "waitlist", "waitlist.json")
        for name, value in (("DATA_DIR", os.path.dirname(waitlist_path)), ("PATH", waitlist_path)):
            patcher = patch.object(self.bot.waitlist, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        for checks in self.built:
            checks.gate.cap.close()
        self.tmp.cleanup()

    def fresh(self):
        """A clean bot state for the next subTest of a loop."""
        self.tearDown()
        self.setUp()

    def build(self, compare_factory, env=None, **kw):
        checks = vision_wiring.build_photo_checks(
            self.env if env is None else env, self.telegram.get_file, self.telegram.fetch,
            data_dir=self.data_dir, photo_dir=self.photo_dir, compare_factory=compare_factory, **kw)
        if checks is not None:
            self.built.append(checks)
        return checks

    def run_updates(self, checks, *updates):
        sent = []
        with patch.object(self.bot, "call", side_effect=lambda method, params: sent.append(params["text"])):
            for u in updates:
                self.bot.handle(u, photo_checks=checks)
        return sent

    def pair(self, checks, lang="ru", user=USER):
        return self.run_updates(checks, update(user, lang, photo="gen-file-id-1"),
                                update(user, lang, photo="real-file-id-1"))

    def used(self, checks, user=USER):
        return checks.gate.counter.used(user)

    def ledger(self):
        return json.loads(pathlib.Path(os.path.join(self.data_dir, "spend_cap.json")).read_text(encoding="utf-8"))

    def holds(self):
        """Open holds on the spend cap v2 ledger: {} once every reservation is closed."""
        return self.ledger()["holds"]


class StatusTests(WiringBase):
    """AC1, AC5: reply per status, photo dir empty, counter only on MATCH / MISMATCH."""

    def test_each_status_reply_cleanup_and_counter(self):
        cases = [("MATCH", {"status": "MATCH"}, 1),
                 ("MISMATCH", {"status": "MISMATCH"}, 1),
                 ("NEEDS_REVIEW", DETAIL, 0),
                 ("UNAVAILABLE", {"status": "UNAVAILABLE"}, 0),
                 ("UNAVAILABLE", RuntimeError("provider exploded " + MODEL_TEXT), 0),
                 ("UNAVAILABLE", {"status": "garbage"}, 0)]
        for lang in ("ru", "en"):
            for status, result, charged in cases:
                with self.subTest(lang=lang, status=status, result=repr(result)):
                    self.fresh()
                    fake = FakeCompare(self, result)
                    checks = self.build(fake)
                    sent = self.pair(checks, lang)
                    self.assertEqual(sent[0], TEXTS[lang]["ask_real"])
                    self.assertTrue(sent[1].startswith(TEXTS[lang][status]), sent[1])
                    self.assertEqual(fake.calls, [1])
                    self.assertEqual(os.listdir(self.photo_dir), [])
                    self.assertEqual(self.used(checks), charged)
                    self.assertEqual(self.holds(), {})   # reservation closed

    def test_needs_review_reply_carries_d11_line_and_to_check_list(self):
        for lang, line, item in (("ru", D11_RU, "Проверьте сами: застёжка (не видно)."),
                                 ("en", D11_EN, "Check yourself: застёжка (not visible).")):
            with self.subTest(lang=lang):
                self.fresh()
                checks = self.build(FakeCompare(self, DETAIL))
                reply = self.pair(checks, lang)[1]
                self.assertIn(line, reply)
                self.assertIn(item, reply)
                self.assertEqual(self.used(checks), 0)

    def test_cleanup_failure_sends_nothing_and_does_not_charge(self):
        checks = self.build(FakeCompare(self, {"status": "MATCH"}))
        real_rmtree = vision_wiring.session_factory.shutil.rmtree
        with patch.object(vision_wiring.session_factory.shutil, "rmtree", side_effect=OSError("disk")):
            sent = self.pair(checks)
        self.assertEqual(sent, [TEXTS["ru"]["ask_real"]])
        self.assertEqual(self.used(checks), 0)
        self.assertEqual(self.holds(), {})
        for leftover in os.listdir(self.photo_dir):   # the files themselves were removed
            self.assertEqual(os.listdir(os.path.join(self.photo_dir, leftover)), [])
            real_rmtree(os.path.join(self.photo_dir, leftover))

    def test_download_failure_is_unavailable_uncharged_and_clean(self):
        fake = FakeCompare(self, {"status": "MATCH"})
        def broken(path):
            yield b"\xff\xd8\xff partial"
            raise OSError("net")
        self.telegram.fetch = broken
        checks = self.build(fake)
        sent = self.pair(checks)
        self.assertEqual(sent[1], TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(os.listdir(self.photo_dir), [])
        self.assertEqual(self.used(checks), 0)
        self.assertEqual(self.holds(), {})


class FreeCounterTests(WiringBase):
    """AC5a: the check after the free ones (FREE_CHECKS, 50 since 14.10) is refused without a
    provider call; no raw id in the stored file."""

    def test_check_after_the_free_ones_gets_waitlist_text_and_no_compare_call(self):
        free = check_counter.FREE_CHECKS
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.fresh()
                fake = FakeCompare(self, {"status": "MISMATCH"})
                checks = self.build(fake)
                for _ in range(free):
                    self.pair(checks, lang)
                self.assertEqual(self.used(checks), free)
                gets = len(self.telegram.get_calls)
                with patch.object(checks.gate.cap, "reserve", wraps=checks.gate.cap.reserve) as reserve:
                    sent = self.pair(checks, lang)
                self.assertEqual(sent[1], TEXTS[lang]["free_used_up"])
                self.assertEqual(fake.calls, [1] * free)
                reserve.assert_not_called()                       # counter is asked before the cap
                self.assertEqual(len(self.telegram.get_calls), gets)  # nothing downloaded
                self.assertEqual(os.listdir(self.photo_dir), [])
                self.assertEqual(self.used(checks), free)
                self.assertEqual(self.used(checks, USER + 1), 0)

    def test_needs_review_and_unavailable_never_use_up_free_checks(self):
        fake = FakeCompare(self, DETAIL)
        # 10 uncharged checks below, then one MATCH: the WP-BOT-10 daily limit (default 10) is raised to 11
        checks = self.build(fake, env=dict(self.env, UNCHARGED_DAILY_LIMIT="11"))
        for _ in range(5):
            self.pair(checks)
        fake.result = {"status": "UNAVAILABLE"}
        for _ in range(5):
            self.pair(checks)
        self.assertEqual(self.used(checks), 0)
        fake.result = {"status": "MATCH"}
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["MATCH"]))

    def test_stored_file_has_no_raw_id_or_names(self):
        checks = self.build(FakeCompare(self, {"status": "MATCH"}))
        self.run_updates(checks, update(text="/start campaign_x"))
        self.pair(checks)
        raw = pathlib.Path(os.path.join(self.data_dir, check_counter.FILE_NAME)).read_bytes()
        for needle in (str(USER), NAME, USERNAME, "gen-file-id-1", "real-file-id-1"):
            self.assertNotIn(needle.encode(), raw)
        rows = json.loads(raw)
        self.assertEqual([set(r) for r in rows], [{"key", "used", "last"}])
        self.assertEqual(rows[0]["used"], 1)

    def test_purge_runs_from_the_polling_tick(self):
        now = [1_791_590_400.0]   # 2026-10-09 12:00 UTC
        checks = self.build(FakeCompare(self, {"status": "MATCH"}), clock=lambda: now[0])
        self.pair(checks)
        now[0] += 89 * 86400
        checks.tick()
        self.assertEqual(self.used(checks), 1)
        now[0] += 2 * 86400
        checks.tick()
        self.assertEqual(self.used(checks), 0)
        self.assertEqual(json.loads(pathlib.Path(os.path.join(self.data_dir, check_counter.FILE_NAME)).read_text(encoding="utf-8")), [])

    def test_main_loop_ticks(self):
        checks = self.build(FakeCompare(self, {"status": "MATCH"}))
        replies = iter([[], KeyboardInterrupt()])
        def call(method, params=None, timeout=None):
            value = next(replies)
            if isinstance(value, BaseException):
                raise value
            return value
        with patch.object(self.bot, "call", side_effect=call), patch.object(checks, "tick") as tick, \
                patch.object(self.bot.logging, "basicConfig"):
            with self.assertRaises(KeyboardInterrupt):
                self.bot.main(photo_checks=checks)
        tick.assert_called_once()

    def test_main_attaches_codes_log_rotates_and_detaches(self):
        from app import codes_log
        path = os.path.join(self.data_dir, codes_log.FILE_NAME)
        os.makedirs(self.data_dir, exist_ok=True)
        stale = "2026-01-01 check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n\n"
        pathlib.Path(path).write_text(stale, encoding="ascii")
        seen = []
        def call(method, params=None, timeout=None):
            handlers = [h for h in logging.getLogger().handlers if isinstance(h, codes_log.CodesLogHandler)]
            seen.append(len(handlers))
            raise KeyboardInterrupt()
        with patch.object(self.bot, "call", side_effect=call), patch.object(self.bot.logging, "basicConfig"), \
                patch.dict(os.environ, {"DATA_DIR": self.data_dir}):
            with self.assertRaises(KeyboardInterrupt):
                self.bot.main(photo_checks=None)
        self.assertEqual(seen, [1])
        self.assertEqual(pathlib.Path(path).read_text(encoding="ascii"), "")      # startup rotation
        self.assertEqual([h for h in logging.getLogger().handlers if isinstance(h, codes_log.CodesLogHandler)], [])

    def _run_main_with_handler(self, handler, calls, **extra):
        from app import codes_log
        replies = iter(calls)
        def call(method, params=None, timeout=None):
            value = next(replies)
            if callable(value):
                value = value()
            if isinstance(value, BaseException):
                raise value
            return value
        with patch.object(self.bot, "call", side_effect=call), patch.object(self.bot.logging, "basicConfig"), \
                patch.object(self.bot.waitlist, "startup"), patch.object(self.bot.waitlist, "maybe_purge"), \
                patch.object(codes_log, "attach", return_value=handler):
            with self.assertRaises(KeyboardInterrupt):
                self.bot.main(photo_checks=None)

    def test_main_loop_rotates_codes_log_on_each_tick_across_a_day(self):
        from app import codes_log
        now = [1_791_590_400.0 + 43200]      # 2026-10-10 12:00 UTC
        path = os.path.join(self.data_dir, codes_log.FILE_NAME)
        os.makedirs(self.data_dir, exist_ok=True)
        handler = codes_log.CodesLogHandler(path, clock=lambda: now[0])
        line = "2026-07-13 check_codes v=2 a=MATCH n=2 codes=1MDD011C,2MDD011C;n\n"     # 89 days old
        pathlib.Path(path).write_text(line, encoding="ascii")
        seen = []
        def second_day():
            seen.append(pathlib.Path(path).read_text(encoding="ascii"))     # kept so far
            now[0] += 2 * 86400                                              # now 91 days old
            return []
        self._run_main_with_handler(handler, [lambda: [], second_day, KeyboardInterrupt()])
        self.assertEqual(seen, [line])
        self.assertEqual(pathlib.Path(path).read_text(encoding="ascii"), "")

    def test_main_calls_codes_log_startup_and_each_tick(self):
        from app import codes_log
        handler = codes_log.CodesLogHandler(os.path.join(self.data_dir, "codes.log"))
        with patch.object(handler, "startup") as startup, patch.object(handler, "maybe_rotate") as tick:
            self._run_main_with_handler(handler, [[], [], KeyboardInterrupt()])
        startup.assert_called_once()
        self.assertEqual(tick.call_count, 3)

    def test_main_startup_removes_stale_temp_and_tightens_mode(self):
        from app import codes_log
        os.makedirs(self.data_dir, exist_ok=True)
        path = os.path.join(self.data_dir, "codes.log")
        stale = os.path.join(self.data_dir, ".codes.x.tmp")
        pathlib.Path(stale).write_text("x")
        pathlib.Path(path).write_text("")
        os.chmod(path, 0o644)
        handler = codes_log.CodesLogHandler(path)
        self._run_main_with_handler(handler, [KeyboardInterrupt()])
        self.assertFalse(os.path.exists(stale))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_missing_or_short_salt_keeps_feature_off(self):
        for salt in (None, "", "s" * 31):
            with self.subTest(salt=salt):
                env = dict(VISION_ENV)
                if salt is not None:
                    env["CHECK_COUNTER_SALT"] = salt
                fake = FakeCompare(self, {"status": "MATCH"})
                with self.assertLogs("check_counter", "WARNING") as logs:
                    self.assertIsNone(self.build(fake, env=env))
                self.assertNotIn("s" * 10, repr(logs.output))
                self.assertFalse(os.path.exists(self.data_dir))   # no counter file, no cap created


class SpendCapTests(WiringBase):
    """AC6: no provider call when the cap refuses; one cap at startup; SECOND_WRITER -> off."""

    def test_cap_refusal_replies_d7_no_compare_no_download_no_charge(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.fresh()
                fake = FakeCompare(self, {"status": "MATCH"})
                checks = self.build(fake, env=dict(self.env, VISION_DAILY_CAP_USD="0"))
                sent = self.pair(checks, lang)
                self.assertEqual(sent[1], TEXTS[lang]["limit_reached"])
                self.assertEqual(fake.calls, [])
                self.assertEqual(self.telegram.get_calls, [])
                self.assertEqual(os.listdir(self.photo_dir), [])
                self.assertEqual(self.used(checks), 0)

    def test_cap_reached_after_spending(self):
        # 0.10 USD cap, hold 0.06: the 1st check fits; it records 0.05, so the 2nd (0.05 + 0.06) is refused.
        fake = FakeCompare(self, {"status": "MATCH"}, costs=(0.02, 0.02, 0.01))
        checks = self.build(fake, env=dict(self.env, VISION_DAILY_CAP_USD="0.10"))
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["MATCH"]))
        self.assertEqual(self.pair(checks, user=USER + 1)[1], TEXTS["ru"]["limit_reached"])
        self.assertEqual(fake.calls, [1])
        ledger = json.loads(pathlib.Path(os.path.join(self.data_dir, "spend_cap.json")).read_text(encoding="utf-8"))
        self.assertAlmostEqual(ledger["spent_usd"], 0.05)

    def test_overrun_stops_the_check_unavailable_uncharged(self):
        # Board post 0008681a: three 0.05 calls under a 0.10 cap would record 0.15.
        fake = FakeCompare(self, {"status": "MATCH"}, costs=(0.05, 0.05, 0.05))
        checks = self.build(fake, env=dict(self.env, VISION_DAILY_CAP_USD="0.10"))
        with self.assertLogs("vision_wiring", "WARNING") as logs:
            reply = self.pair(checks)[1]
        self.assertEqual(reply, TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(fake.provider_calls, 2)
        self.assertIn("spend_cap_overrun", "\n".join(logs.output))
        self.assertEqual(self.used(checks), 0)
        self.assertEqual(os.listdir(self.photo_dir), [])
        self.assertEqual(self.holds(), {})

    def test_overrun_on_last_call_is_still_unavailable(self):
        fake = FakeCompare(self, {"status": "MATCH"}, costs=(0.01, 0.01, 0.05), obey=False)
        checks = self.build(fake)
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(self.used(checks), 0)

    def test_day_total_past_cap_stops_the_check(self):
        # Today's ledger goes past the cap while the first request is out (a write by
        # another source): the check's own total is still under its hold, the day total
        # is not. Spend cap v2 sees the file changed behind its back (LEDGER_CHANGED),
        # keeps the record in memory and leaves the file, so the overrun reader sees it.
        fake = FakeCompare(self, {"status": "MATCH"}, costs=(0.001, 0.001, 0.001))
        ledger = os.path.join(self.data_dir, "spend_cap.json")
        checks = self.build(fake, env=dict(self.env, VISION_DAILY_CAP_USD="0.10"))
        begin = fake.on_begin
        def begin_then_foreign_write():
            ok = begin()
            today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            pathlib.Path(ledger).write_text(json.dumps({"day": today, "spent_usd": 0.1005}))
            return ok
        fake.on_begin = begin_then_foreign_write
        with self.assertLogs("vision_wiring", "WARNING") as logs, \
                self.assertLogs("spend_cap", "ERROR") as cap_logs:
            reply = self.pair(checks)[1]
        self.assertEqual(reply, TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(fake.provider_calls, 1)
        self.assertIn("spend_cap_overrun", "\n".join(logs.output))
        self.assertIn("spend_cap_error code=LEDGER_CHANGED", "\n".join(cap_logs.output))
        self.assertEqual(self.used(checks), 0)
        self.assertEqual(os.listdir(self.photo_dir), [])

    def test_unreadable_ledger_after_record_stops_the_check(self):
        fake = FakeCompare(self, {"status": "MATCH"})
        checks = self.build(fake)
        with patch.object(vision_wiring, "_ledger_micro", return_value=None):
            self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual((fake.provider_calls, self.used(checks)), (1, 0))

    def test_compare_ignoring_the_stop_gets_no_verdict(self):
        fake = FakeCompare(self, {"status": "MATCH"}, costs=(0.05, 0.05, 0.05), obey=False)
        checks = self.build(fake)
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(self.used(checks), 0)

    def test_exactly_at_hold_is_not_an_overrun(self):
        fake = FakeCompare(self, {"status": "MATCH"}, costs=(0.02, 0.02, 0.02))
        checks = self.build(fake)
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["MATCH"]))
        self.assertEqual((fake.provider_calls, self.used(checks)), (3, 1))

    def test_cap_created_once_at_startup(self):
        with patch.object(vision_wiring.spend_cap, "SpendCap", wraps=spend_cap.SpendCap) as cls:
            checks = self.build(FakeCompare(self, {"status": "MISMATCH"}))
            for user in (USER, USER + 1, USER + 2):
                self.pair(checks, user=user)
        cls.assert_called_once()
        args, kwargs = cls.call_args
        self.assertEqual(args[0], os.path.join(self.data_dir, "spend_cap.json"))
        self.assertEqual(args[1], 2.0)
        self.assertEqual((kwargs["per_call_fallback_usd"], kwargs["calls_per_check"]), (0.02, 3))

    def test_second_writer_turns_feature_off(self):
        os.makedirs(self.data_dir)
        holder = spend_cap.SpendCap(os.path.join(self.data_dir, "spend_cap.json"), 2.0)
        try:
            with self.assertLogs("vision_wiring", "ERROR") as logs:
                self.assertIsNone(self.build(FakeCompare(self, {"status": "MATCH"})))
            self.assertIn("spend_cap_error code=SECOND_WRITER", logs.output[0])
        finally:
            holder.close()

    def test_invalid_cap_config_turns_feature_off(self):
        with self.assertLogs("vision_wiring", "ERROR") as logs:
            self.assertIsNone(self.build(FakeCompare(self, {"status": "MATCH"}),
                                         env=dict(self.env, VISION_DAILY_CAP_USD="lots")))
        self.assertIn("spend_cap_error code=CONFIG", logs.output[0])

    def test_guarded_compare_never_calls_without_reservation(self):
        fake = FakeCompare(self, {"status": "MATCH"})
        checks = self.build(fake)
        self.assertEqual(checks.compare(PNG, JPEG), {"status": "UNAVAILABLE"})
        self.assertEqual(fake.calls, [])


class RealChainCostTests(WiringBase):
    """record(usage.cost) after each of the three real provider calls (fake transport)."""

    def chain(self, model):
        return lambda environ, **hooks: vision_compare.compare_from_env(
            environ, transport=model, **hooks)

    def spent(self):
        return json.loads(pathlib.Path(os.path.join(self.data_dir, "spend_cap.json")).read_text(encoding="utf-8"))["spent_usd"]

    def test_each_call_cost_is_recorded(self):
        model = FakeModel(usage={"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.004})
        checks = self.build(self.chain(model))
        with patch.object(spend_cap.Reservation, "record", autospec=True,
                          side_effect=spend_cap.Reservation.record) as record:
            reply = self.pair(checks)[1]
        self.assertTrue(reply.startswith(TEXTS["ru"]["MATCH"]), reply)
        self.assertEqual(len(model.requests), 3)
        self.assertEqual([c.args[1] for c in record.call_args_list], [0.004, 0.004, 0.004])
        self.assertAlmostEqual(self.spent(), 0.012)
        self.assertEqual(self.used(checks), 1)
        self.assertEqual(os.listdir(self.photo_dir), [])

    def test_real_chain_overrun_makes_two_calls_at_most(self):
        model = FakeModel(usage={"cost": 0.05})
        checks = self.build(self.chain(model), env=dict(self.env, VISION_DAILY_CAP_USD="0.10"))
        with self.assertLogs(level=logging.INFO) as logs:
            reply = self.pair(checks)[1]
        text = "\n".join(logs.output)
        self.assertEqual(reply, TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(len(model.requests), 2)
        self.assertIn("spend_cap_overrun", text)
        self.assertIn("code=SPEND_CAP_OVERRUN", text)
        self.assertAlmostEqual(self.spent(), 0.10)
        self.assertEqual(self.used(checks), 0)
        self.assertEqual(os.listdir(self.photo_dir), [])
        self.assertEqual(self.pair(checks, user=USER + 1)[1], TEXTS["ru"]["limit_reached"])
        self.assertEqual(len(model.requests), 2)

    def test_hook_error_stops_the_chain(self):
        model = FakeModel(usage={"cost": 0.004})
        def broken_factory(environ, on_call=None, **hooks):
            def hook(cost):
                raise RuntimeError("hook")
            return vision_compare.compare_from_env(environ, transport=model, on_call=hook, **hooks)
        checks = self.build(broken_factory)
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(len(model.requests), 1)

    def test_missing_usage_counts_fallback_per_call(self):
        checks = self.build(self.chain(FakeModel()))
        self.pair(checks)
        self.assertAlmostEqual(self.spent(), 0.06)

    def test_failed_call_counts_fallback_and_stops_chain(self):
        model = FakeModel(usage={"cost": 0.004}, fail_at=1, reply=TimeoutError())
        checks = self.build(self.chain(model))
        reply = self.pair(checks)[1]
        self.assertEqual(reply, TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(len(model.requests), 2)
        self.assertAlmostEqual(self.spent(), 0.024)
        self.assertEqual(self.used(checks), 0)
        self.assertEqual(os.listdir(self.photo_dir), [])

    def test_logs_carry_no_identifiers_or_model_text(self):
        model = FakeModel(usage={"cost": 0.004})
        checks = self.build(self.chain(model))
        with self.assertLogs(level=logging.DEBUG) as logs:
            self.run_updates(checks, update(text="/start"))
            self.pair(checks)
            self.pair(checks, user=USER)
            logging.getLogger("probe").info("probe")
        text = "\n".join(logs.output)
        for needle in (str(USER), NAME, USERNAME, "gen-file-id-1", "real-file-id-1", "photos/",
                       self.photo_dir, SALT, KEY, MODEL_TEXT, "пуговицы", "SECRET",
                       checks.gate.counter.key(USER)):
            self.assertNotIn(needle, text)
        self.assertIn("check_done status=MATCH charged=yes", text)


class PrivacyAndOffTests(WiringBase):
    """AC2: privacy line before or with the first upload prompt; AC3: VISION_* unset = main."""

    def test_privacy_before_first_prompt_ru_en(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.fresh()
                checks = self.build(FakeCompare(self, {"status": "MATCH"}))
                sent = self.run_updates(checks, update(lang=lang, text="/start"))
                t = TEXTS[lang]
                self.assertEqual(sent, [t["welcome_checks"], t["privacy"], t["ask_generated"]])
                self.assertLess(sent.index(t["privacy"]), sent.index(t["ask_generated"]))
                self.assertIn("OpenRouter", t["privacy"])
                self.assertIn("google/gemini-3.6-flash", t["privacy"])
                self.assertIn("/privacy", t["ask_real"])
                self.assertNotIn("Скоро", t["welcome_checks"])
                self.assertNotIn("Soon", t["welcome_checks"])
                self.assertEqual(self.run_updates(checks, update(lang=lang, text="/privacy")), [t["privacy"]])
                self.assertEqual(self.run_updates(checks, update(lang=lang, text="/help")),
                                 [t["welcome_checks"], t["privacy"]])

    def test_privacy_line_texts(self):
        self.assertTrue(TEXTS["ru"]["privacy"].endswith(
            "Бот 90 дней хранит число ваших проверок под обезличенным ключом и обезличенные коды проверок без привязки к вам. Ещё бот помнит ваш Telegram ID "
            "и откуда вы пришли — для списка ожидания платных проверок. /forget удаляет всё о вас."))
        self.assertTrue(TEXTS["en"]["privacy"].endswith(
            "For 90 days the bot keeps the number of your checks under an anonymised key and anonymised check codes not linked to you. It also keeps your "
            "Telegram ID and where you came from, for the paid-checks waitlist. /forget deletes everything about you."))
        for lang in ("ru", "en"):
            self.assertNotIn("только", TEXTS[lang]["privacy"])
            self.assertNotIn("only", TEXTS[lang]["privacy"])
        for lang in ("ru", "en"):
            self.assertIn("(UTC)", TEXTS[lang]["limit_reached"])
        self.assertTrue(TEXTS["ru"]["limit_reached"].startswith("Дневной лимит проверок исчерпан, попробуйте завтра (UTC)"))
        self.assertTrue(TEXTS["en"]["limit_reached"].startswith("Daily check limit reached, try again tomorrow (UTC)"))

    def test_real_photo_first_gets_privacy_pointer_before_next_photo(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.fresh()
                fake = FakeCompare(self, {"status": "MATCH"})
                checks = self.build(fake)
                sent = self.run_updates(checks, update(lang=lang, photo="real-file-id-1", caption="real"))
                self.assertEqual(sent, [TEXTS[lang]["ask_generated_after_real"]])
                self.assertIn("/privacy", sent[0])
                self.assertIn("OpenRouter", sent[0])
                self.assertEqual(fake.calls, [])
                sent = self.run_updates(checks, update(lang=lang, photo="gen-file-id-1"))
                self.assertTrue(sent[0].startswith(TEXTS[lang]["MATCH"]))

    def test_help_keeps_half_pair(self):
        fake = FakeCompare(self, {"status": "MATCH"})
        checks = self.build(fake)
        sent = self.run_updates(checks, update(photo="gen-file-id-1"), update(text="/help"),
                                update(photo="real-file-id-1"))
        self.assertTrue(sent[-1].startswith(TEXTS["ru"]["MATCH"]))

    def test_vision_unset_replies_exactly_as_main(self):
        env = {"CHECK_COUNTER_SALT": SALT}
        with patch.dict(os.environ, {}, clear=False):
            self.assertIsNone(vision_wiring.build_photo_checks(env, self.telegram.get_file,
                                                               self.telegram.fetch, data_dir=self.data_dir,
                                                               photo_dir=self.photo_dir))
            self.assertIsNone(self.bot.build({}))
        self.assertFalse(os.path.exists(self.data_dir))
        for lang in ("ru", "en"):
            with self.subTest(lang=lang), patch.object(self.bot.waitlist, "record", return_value=("direct", False)):
                sent = self.run_updates(None, update(lang=lang, text="/start"), update(lang=lang, photo="gen-file-id-1"),
                                        update(lang=lang, text="hello"), update(lang=lang, text="/help"))
                m = MAIN_TEXTS[lang]
                self.assertEqual(sent, [m["welcome"], m["fallback"], m["fallback"], m["fallback"]])
        self.assertEqual(self.telegram.get_calls, [])

    def test_invalid_vision_config_keeps_feature_off(self):
        with self.assertLogs("vision_wiring", "WARNING"):
            self.assertIsNone(vision_wiring.build_photo_checks(
                dict(self.env, VISION_MODEL="bad model!"), self.telegram.get_file, self.telegram.fetch,
                data_dir=self.data_dir, photo_dir=self.photo_dir))


class FetchFileTests(unittest.TestCase):
    def setUp(self):
        self.bot = load_bot()

    def test_rejects_unsafe_paths_before_any_request(self):
        with patch.object(self.bot.urllib.request, "urlopen") as urlopen:
            for bad in (None, "", "/etc/passwd", "photos/../x", 5):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    list(self.bot.fetch_file(bad))
        urlopen.assert_not_called()

    def test_streams_chunks_from_file_endpoint(self):
        class Resp:
            def __init__(self):
                self.parts = [b"ab", b"cd", b""]
            def read(self, n):
                return self.parts.pop(0)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        with patch.object(self.bot.urllib.request, "urlopen", return_value=Resp()) as urlopen:
            self.assertEqual(list(self.bot.fetch_file("photos/file_1.jpg")), [b"ab", b"cd"])
        self.assertEqual(urlopen.call_args.args[0],
                         "https://api.telegram.org/file/botunit-test-placeholder/photos/file_1.jpg")

    def test_get_file_calls_bot_api(self):
        with patch.object(self.bot, "call", return_value={"file_path": "p"}) as call:
            self.assertEqual(self.bot.get_file("fid"), {"file_path": "p"})
        self.assertEqual(call.call_args.args[:2], ("getFile", {"file_id": "fid"}))


if __name__ == "__main__":
    unittest.main()
