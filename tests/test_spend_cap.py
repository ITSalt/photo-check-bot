import json, math, os, stat, subprocess, sys, tempfile, threading, time, traceback, unittest
from app.spend_cap import SpendCap, Reservation, CapConfigError, SingleWriterError, cap_from_env

DAY1 = 1791504000.0          # 2026-10-09 00:00:00 UTC
HERE = os.path.dirname(os.path.abspath(__file__))


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "ledger.json")
        self.clock = Clock(DAY1 + 3600)

    def cap(self, cap_usd=2.00, **kw):
        c = SpendCap(self.path, cap_usd, clock=kw.pop("clock", self.clock), **kw)
        self.addCleanup(c.close)
        return c

    def ledger(self):
        with open(self.path) as f: return json.load(f)


class SlowHold(SpendCap):
    """Test hook: every read of the hold size yields the CPU, so a reserve() without its lock lets other
    threads in between the cap check and taking the hold (under the GIL that window is otherwise tiny)."""
    @property
    def _hold_size(self):
        time.sleep(0.002); return self.__dict__["_hs"]

    @_hold_size.setter
    def _hold_size(self, v):
        self.__dict__["_hs"] = v


class Acceptance(Base):
    def test_1_33_granted_34th_refused(self):
        c = self.cap(2.00, per_call_fallback_usd=0.02, calls_per_check=3)
        rs = [c.reserve() for _ in range(33)]
        self.assertTrue(all(isinstance(r, Reservation) for r in rs))
        self.assertIsNone(c.reserve())
        rs[0].close()
        self.assertIsNotNone(c.reserve())              # a released hold frees exactly one slot

    def test_2_cheap_records_then_close_fit_more(self):
        c = self.cap(2.00)
        rs = [c.reserve() for _ in range(33)]
        self.assertIsNone(c.reserve())
        for r in rs:
            r.record(0.001); r.close()
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.033, places=9)
        more = 0
        while c.reserve() is not None: more += 1
        self.assertEqual(more, 32)                      # (2.00 - 0.033) // 0.06

    def test_3_midnight_resets(self):
        c = self.cap(0.12)
        for _ in range(2):
            with c.reserve() as r: r.record(0.06)
        self.assertIsNone(c.reserve())
        self.clock.t = DAY1 + 86400 - 1
        self.assertIsNone(c.reserve())                  # still the same UTC day
        self.clock.t = DAY1 + 86400 + 1
        r = c.reserve(); self.assertIsNotNone(r)
        r.record(0.01); r.close()
        self.assertEqual(self.ledger(), {"day": "2026-10-10", "spent_usd": 0.01})

    def test_4_corrupt_and_missing(self):
        c = self.cap()
        self.assertIsNotNone(c.reserve())               # missing ledger = 0 today
        secret = "user=4242 chat=99 photo=AgADxyz"
        for bad in ("{not json " + secret, json.dumps([1, 2]), json.dumps({"day": "2026-10-09"}),
                    json.dumps({"day": "2026-10-09", "spent_usd": "1", "x": secret}),
                    json.dumps({"day": "yesterday", "spent_usd": 0.1}),
                    json.dumps({"day": "2026-10-09", "spent_usd": -1}),
                    json.dumps({"day": "2099-01-01", "spent_usd": 0.0}),
                    "\xff\xfe"):
            with open(self.path, "w", encoding="latin-1") as f: f.write(bad)
            with self.assertLogs("spend_cap", "ERROR") as cm:
                self.assertIsNone(c.reserve())
            self.assertEqual(len(cm.records), 1)
            self.assertEqual(cm.records[0].getMessage(), "spend_cap_error code=LEDGER_CORRUPT")

    def test_5_race_for_last_slot(self):
        for _ in range(20):
            os.path.exists(self.path) and os.unlink(self.path)
            c = SlowHold(self.path, 0.06)
            try:
                got, gate = [], threading.Barrier(8)
                def go():
                    gate.wait(); got.append(c.reserve())
                ts = [threading.Thread(target=go) for _ in range(8)]
                [t.start() for t in ts]; [t.join() for t in ts]
                self.assertEqual(sum(r is not None for r in got), 1)
            finally:
                c.close()

    def test_6_bad_costs_count_as_fallback(self):
        c = self.cap()
        with c.reserve() as r:
            for bad in (None, float("nan"), "abc", -1, True, float("inf"), [0.01]):
                r.record(bad)
        self.assertAlmostEqual(self.ledger()["spent_usd"], 7 * 0.02, places=9)

    def test_7_ledger_shape_and_mode(self):
        c = self.cap()
        with c.reserve() as r: r.record(0.0123)
        self.assertEqual(set(self.ledger()), {"day", "spent_usd"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["ledger.json", "ledger.json.lock"])  # no temp left

    def test_8_cap_from_env(self):
        self.assertEqual(cap_from_env({}), 2.0)
        self.assertEqual(cap_from_env({"VISION_DAILY_CAP_USD": ""}), 2.0)
        self.assertEqual(cap_from_env({"VISION_DAILY_CAP_USD": "0.5"}), 0.5)
        zero = cap_from_env({"VISION_DAILY_CAP_USD": "0"})
        self.assertEqual(zero, 0.0)
        c = self.cap(zero)
        self.assertTrue(all(c.reserve() is None for _ in range(3)))
        for bad in ("abc", "-1", "inf", "nan", "-inf", "1e999"):
            with self.assertRaises(CapConfigError) as cm:
                cap_from_env({"VISION_DAILY_CAP_USD": bad})
            self.assertNotIn(bad, str(cm.exception))
            shown = "".join(traceback.format_exception(cm.exception))   # what a crash log would print
            self.assertNotIn(repr(bad), shown)

    def test_9_single_writer_across_processes(self):
        child = ("import sys; import os; sys.path.insert(0, os.path.dirname(sys.argv[1])); from app.spend_cap import *\n"
                 "try:\n  SpendCap(sys.argv[2], 1.0).close()\nexcept SingleWriterError as e:\n"
                 "  assert sys.argv[2] not in str(e); sys.exit(3)\n")
        run = lambda: subprocess.run([sys.executable, "-c", child, HERE, self.path], timeout=30).returncode
        a = SpendCap(self.path, 1.0)
        self.assertEqual(run(), 3)
        a.close()
        self.assertEqual(run(), 0)
        self.assertTrue(issubclass(SingleWriterError, CapConfigError))


class Extra(Base):
    def test_recorded_cost_above_hold_is_counted_in_full(self):
        c = self.cap(0.20)
        with c.reserve() as r: r.record(0.15)
        self.assertIsNone(c.reserve())                  # 0.15 + 0.06 > 0.20

    def test_close_is_idempotent_and_record_after_close_still_counts(self):
        c = self.cap(0.12)
        r = c.reserve(); r.close(); r.close()
        r.record(0.05)                                  # money spent is money spent
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.05, places=9)
        self.assertIsNotNone(c.reserve())
        self.assertIsNone(c.reserve())

    def test_write_failure_keeps_cost_counted(self):
        c = self.cap(0.12)
        r = c.reserve()
        os.chmod(self.tmp.name, 0o500)                  # temp file cannot be created
        try:
            if os.access(self.tmp.name, os.W_OK): self.skipTest("running as root")
            with self.assertLogs("spend_cap", "ERROR") as cm:
                r.record(0.06)
            self.assertEqual(cm.output, ["ERROR:spend_cap:spend_cap_error code=LEDGER_WRITE"])
        finally:
            os.chmod(self.tmp.name, 0o700)
        r.close()
        self.assertIsNotNone(c.reserve())               # 0.06 counted in memory, 0.06 left
        self.assertIsNone(c.reserve())

    def test_bad_constructor_arguments(self):
        for kw in ({"cap_usd": -1}, {"cap_usd": float("nan")}, {"cap_usd": True},
                   {"cap_usd": 1, "per_call_fallback_usd": -0.01}, {"cap_usd": 1, "calls_per_check": 0}):
            with self.assertRaises(CapConfigError):
                SpendCap(self.path, **kw).close()


if __name__ == "__main__":
    unittest.main()
