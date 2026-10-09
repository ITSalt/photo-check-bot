"""WP-BOT-12 integration: spend cap v2 (D-17) in the bot. Fakes only: no network, no keys.

begin() before each provider call; a refused begin sends nothing; not_sent() only when the
transport was never invoked; a process killed between begin and record leaves an orphan
that the next start logs and counts; a v1 ledger is read losslessly; settle() always
closes the reservation (3a).
"""
import datetime
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

from app import spend_cap, vision_compare
from app.photo_checks import TEXTS
from tests.test_bot_wiring import USER, WiringBase
from tests.test_t4_vision import FakeModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIE = ("import os, signal, sys; sys.path.insert(0, sys.argv[1]); from app.spend_cap import SpendCap\n"
       "c = SpendCap(sys.argv[2], 2.0); r = c.reserve(); assert r.begin()\n"
       "print(r.hold_id, flush=True); os.kill(os.getpid(), signal.SIGKILL)\n")


def today():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


class V2Base(WiringBase):
    def chain(self, model):
        return lambda environ, **hooks: vision_compare.compare_from_env(environ, transport=model, **hooks)

    def cap_path(self):
        return os.path.join(self.data_dir, "spend_cap.json")


class BeginTests(V2Base):
    def test_begin_false_sends_nothing_unavailable_uncharged(self):
        model = FakeModel(usage={"cost": 0.004})
        checks = self.build(self.chain(model))
        with patch.object(spend_cap.Reservation, "begin", return_value=False), \
                self.assertLogs("vision_wiring", "ERROR") as logs:
            reply = self.pair(checks)[1]
        self.assertEqual(reply, TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(model.requests, [])
        self.assertEqual(self.used(checks), 0)
        self.assertIn("spend_cap_error code=BEGIN", "\n".join(logs.output))
        self.assertEqual((self.ledger()["spent_usd"], self.holds()), (0, {}))

    def test_begin_marks_each_request_before_it_leaves(self):
        model = FakeModel(usage={"cost": 0.004})
        seen = []
        def transport(request, headers, timeout):
            hold = next(iter(self.holds().values()))           # the mark is on disk already
            seen.append(hold["in_flight"])
            return model(request, headers, timeout)
        checks = self.build(self.chain(transport))
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["MATCH"]))
        self.assertEqual(seen, [1, 1, 1])
        self.assertEqual(self.holds(), {})
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.012)

    def test_missing_key_is_not_sent_and_not_counted(self):
        model = FakeModel(usage={"cost": 0.004})
        checks = self.build(self.chain(model))
        del self.env["VISION_API_KEY"]                 # call_provider stops before the transport
        with patch.object(spend_cap.Reservation, "not_sent", autospec=True,
                          side_effect=spend_cap.Reservation.not_sent) as not_sent:
            self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["UNAVAILABLE"])
        self.assertEqual(model.requests, [])
        self.assertEqual(not_sent.call_count, 1)
        self.assertEqual((self.ledger()["spent_usd"], self.holds()), (0, {}))

    def test_provider_error_never_calls_not_sent(self):
        for reply in (TimeoutError(), {"status": 503, "body": None}, OSError("reset")):
            with self.subTest(reply=repr(reply)):
                self.fresh()
                model = FakeModel(usage={"cost": 0.004}, fail_at=0, reply=reply)
                checks = self.build(self.chain(model))
                with patch.object(spend_cap.Reservation, "not_sent") as not_sent:
                    self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["UNAVAILABLE"])
                not_sent.assert_not_called()
                self.assertAlmostEqual(self.ledger()["spent_usd"], 0.02)   # the fallback
                self.assertEqual(self.holds(), {})


class CrashTests(V2Base):
    def die_holding(self):
        os.makedirs(self.data_dir, exist_ok=True)
        p = subprocess.run([sys.executable, "-c", DIE, ROOT, self.cap_path()],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, -signal.SIGKILL)
        return p.stdout.strip()

    def test_killed_between_begin_and_record_is_an_orphan_that_counts(self):
        hold = self.die_holding()                      # request out, process killed, no record()
        model = FakeModel(usage={"cost": 0.004})
        with self.assertLogs("vision_wiring", "INFO") as logs:
            checks = self.build(self.chain(model), env=dict(self.env, VISION_DAILY_CAP_USD="0.10"))
        self.assertIsNotNone(checks)
        orphan = [l for l in logs.output if "spend_cap_orphan" in l]
        self.assertEqual(orphan, ["WARNING:vision_wiring:spend_cap_orphan hold=%s day=%s usd=0.060000"
                                  % (hold, today())])
        self.assertIn("vision enabled", "\n".join(logs.output))
        # 0.06 (dead hold) + 0.06 > 0.10: a check that would pass the cap alone is refused
        self.assertEqual(self.pair(checks)[1], TEXTS["ru"]["limit_reached"])
        self.assertEqual(model.requests, [])
        self.assertEqual(list(self.holds()), [hold])   # never released automatically

    def test_orphan_settled_by_the_operator_frees_the_day(self):
        hold = self.die_holding()
        holder = spend_cap.SpendCap(self.cap_path(), 0.10)
        self.assertEqual(holder.settle(hold, 0.004), "settled")   # the documented command
        holder.close()
        model = FakeModel(usage={"cost": 0.004})
        checks = self.build(self.chain(model), env=dict(self.env, VISION_DAILY_CAP_USD="0.10"))
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["MATCH"]))
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.016)

    def test_documented_settle_command_works(self):
        hold = self.die_holding()
        doc = pathlib.Path(ROOT, "docs", "t4-vision.md").read_text(encoding="utf-8")
        code = re.search(r'python -c "(from app\.spend_cap import SpendCap;[^"]+)"', doc).group(1)
        code = code.replace("/data/spend_cap.json", self.cap_path()).replace("HOLD_ID", hold) \
                   .replace("REAL_COST_USD", "0.004")
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "settled"))
        self.assertEqual(self.holds(), {})
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.004)

    def test_lock_without_ledger_keeps_vision_off(self):
        os.makedirs(self.data_dir)
        pathlib.Path(self.cap_path() + ".lock").touch()
        with self.assertLogs("vision_wiring", "ERROR") as logs:
            self.assertIsNone(self.build(self.chain(FakeModel())))
        self.assertIn("ERROR:vision_wiring:vision disabled, code=LOCK_WITHOUT_LEDGER", logs.output)
        self.assertNotIn("vision enabled", "\n".join(logs.output))
        self.assertFalse(os.path.exists(self.cap_path()))
        os.remove(self.cap_path() + ".lock")           # the documented reset: remove both files
        self.assertIsNotNone(self.build(self.chain(FakeModel())))

    def test_corrupt_ledger_keeps_vision_off(self):
        for raw in ("not json", json.dumps({"day": today(), "spent_usd": -1}),
                    json.dumps({"day": today(), "spent_usd": 0, "holds": {"x": {"day": today()}}})):
            with self.subTest(raw=raw):
                self.fresh()
                os.makedirs(self.data_dir)
                pathlib.Path(self.cap_path()).write_text(raw)
                with self.assertLogs("vision_wiring", "ERROR") as logs:
                    self.assertIsNone(self.build(self.chain(FakeModel())))
                self.assertIn("ERROR:vision_wiring:vision disabled, code=LEDGER_CORRUPT", logs.output)
                self.assertEqual(pathlib.Path(self.cap_path()).read_text(), raw)   # left for a human


class V1LedgerTests(V2Base):
    def test_v1_ledger_is_read_with_no_holds_losslessly(self):
        os.makedirs(self.data_dir)
        pathlib.Path(self.cap_path()).write_text(json.dumps({"day": today(), "spent_usd": 0.033}))
        pathlib.Path(self.cap_path() + ".lock").touch()           # v1 kept a lock file too
        with self.assertLogs("vision_wiring", "INFO") as logs:
            checks = self.build(self.chain(FakeModel(usage={"cost": 0.004})),
                                env=dict(self.env, VISION_DAILY_CAP_USD="0.10"))
        self.assertNotIn("spend_cap_orphan", "\n".join(logs.output))
        self.assertTrue(self.pair(checks)[1].startswith(TEXTS["ru"]["MATCH"]))   # 0.033 + 0.06 <= 0.10
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.045)   # 0.033 kept + 3 x 0.004
        self.assertEqual(self.holds(), {})
        self.assertEqual(self.pair(checks, user=USER + 1)[1], TEXTS["ru"]["limit_reached"])  # 0.045 + 0.06


class SettleTests(V2Base):
    """3a: settle() closes the reservation even when charge() raises a BaseException."""

    def test_raising_charge_still_closes_the_reservation(self):
        checks = self.build(self.chain(FakeModel(usage={"cost": 0.004})))
        gate = checks.gate
        for exc in (KeyboardInterrupt, SystemExit, RuntimeError):
            with self.subTest(exc=exc.__name__):
                ticket, refusal = gate.admit(USER)
                self.assertIsNone(refusal)
                self.assertEqual(len(self.holds()), 1)
                with patch.object(gate.counter, "charge", side_effect=exc("charge")):
                    if issubclass(exc, Exception):
                        gate.settle(ticket, "MATCH")              # logged, not raised
                    else:
                        with self.assertRaises(exc):
                            gate.settle(ticket, "MATCH")
                self.assertEqual(self.holds(), {})
                self.assertIsNone(gate._active())


if __name__ == "__main__":
    unittest.main()
