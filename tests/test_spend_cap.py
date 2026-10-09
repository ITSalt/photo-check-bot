import json, math, os, signal, stat, subprocess, sys, tempfile, threading, time, traceback, unittest
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
        self.assertEqual(self.ledger(), {"day": "2026-10-10", "spent_usd": 0.01, "holds": {}})

    def test_4_corrupt_and_missing(self):
        c = self.cap()
        self.assertIsNotNone(c.reserve())               # missing ledger = 0 today
        c.close()
        secret = "user=4242 chat=99 photo=AgADxyz"
        for bad in ("{not json " + secret, json.dumps([1, 2]), json.dumps({"day": "2026-10-09"}),
                    json.dumps({"day": "2026-10-09", "spent_usd": "1", "x": secret}),
                    json.dumps({"day": "yesterday", "spent_usd": 0.1}),
                    json.dumps({"day": "2026-10-09", "spent_usd": -1}),
                    json.dumps({"day": "2099-01-01", "spent_usd": 0.0}),
                    json.dumps({"day": "2026-10-09", "spent_usd": 0.0, "holds": []}),
                    json.dumps({"day": "2026-10-09", "spent_usd": 0.0, "holds": {"a": {"day": "2026-10-09", "usd": 0.06}}}),
                    json.dumps({"day": "2026-10-09", "spent_usd": 0.0, "holds": {"a": {"day": "2026-10-09", "usd": -1, "in_flight": 0}}}),
                    json.dumps({"day": "2026-10-09", "spent_usd": 0.0, "holds": {"a": {"day": "2026-10-09", "usd": 0.06, "in_flight": True}}}),
                    json.dumps({"day": "2026-10-09", "spent_usd": 0.0, "holds": {"a": {"day": "2099-01-01", "usd": 0.06, "in_flight": 0}}}),
                    "\xff\xfe"):
            with open(self.path, "w", encoding="latin-1") as f: f.write(bad)
            c = SpendCap(self.path, 2.00, clock=self.clock)   # a new owner must refuse it on parse
            with self.assertLogs("spend_cap", "ERROR") as cm:
                self.assertIsNone(c.reserve())
            c.close()
            self.assertEqual(len(cm.records), 1)
            self.assertEqual(cm.records[0].getMessage(), "spend_cap_error code=LEDGER_CORRUPT")

    def test_5_race_for_last_slot(self):
        for _ in range(20):
            for p in (self.path, self.path + ".lock"):  # start from nothing each round
                os.path.exists(p) and os.unlink(p)
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
        self.assertEqual(self.ledger(), {"day": "2026-10-09", "spent_usd": 0.0123, "holds": {}})
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
            self.assertEqual(cm.output, [f"ERROR:spend_cap:spend_cap_error code=LEDGER_WRITE hold={r.hold_id} usd=0.060000"])
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


class DurableHolds(Base):
    """v2: a hold is on disk before reserve() returns, and only evidence frees it."""
    DIE = ("import os, signal, sys; sys.path.insert(0, os.path.dirname(sys.argv[1])); from app.spend_cap import SpendCap\n"
           "c = SpendCap(sys.argv[2], 0.10, clock=lambda: float(sys.argv[3])); r = c.reserve()\n"
           "if sys.argv[4] == 'sent': assert r.begin()\n"
           "print(r.hold_id, flush=True); os.kill(os.getpid(), signal.SIGKILL)\n")

    def die_holding(self, stage):
        p = subprocess.run([sys.executable, "-c", self.DIE, HERE, self.path, repr(self.clock.t), stage],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, -signal.SIGKILL)
        return p.stdout.strip()

    def test_10_restart_counts_dead_hold_until_settled(self):
        hid = self.die_holding("sent")                  # A: hold, request out, SIGKILL before record()
        b = self.cap(0.10)
        self.assertIsNone(b.reserve())                  # 0.06 (A, dead) + 0.06 > 0.10
        self.assertEqual(b.orphans(), [(hid, "2026-10-09", 0.06)])
        self.assertEqual(b.settle(hid, 0.005), "settled")  # A's late charge, from the provider's usage
        self.assertEqual(b.orphans(), [])
        self.assertIsNotNone(b.reserve())               # 0.005 + 0.06 <= 0.10
        self.assertIsNone(b.reserve())

    def test_11_dead_before_send_is_still_spent(self):
        hid = self.die_holding("held")                  # A died between reserve() and begin()
        b = self.cap(0.10)
        self.assertIsNone(b.reserve())                  # no evidence it was not sent: counted in full
        b.close(); b = self.cap(0.10)                   # a second restart does not forget it either
        self.assertIsNone(b.reserve())
        self.assertEqual([o[0] for o in b.orphans()], [hid])

    def test_12_close_with_request_in_flight_keeps_hold(self):
        c = self.cap(0.10)
        with self.assertRaises(TimeoutError):
            with c.reserve() as r:
                self.assertTrue(r.begin())
                raise TimeoutError                      # the send timed out: it may still bill
        self.assertIsNone(c.reserve())                  # close() did not free it
        self.assertEqual(len(c.orphans()), 1)
        r.record(0.004)                                 # the late response arrives after all
        self.assertEqual(c.orphans(), [])
        self.assertIsNotNone(c.reserve())

    def test_13_not_sent_and_recorded_calls_free_the_rest(self):
        c = self.cap(0.10)
        with c.reserve() as r:
            self.assertTrue(r.begin()); r.record(0.01)
            self.assertTrue(r.begin()); r.not_sent()    # connection refused: provably never left
        self.assertEqual(self.ledger(), {"day": "2026-10-09", "spent_usd": 0.01, "holds": {}})
        self.assertIsNotNone(c.reserve())

    def test_14_hold_is_on_disk_before_reserve_returns(self):
        c = self.cap(0.10)
        r = c.reserve()
        self.assertEqual(self.ledger()["holds"], {r.hold_id: {"day": "2026-10-09", "usd": 0.06, "in_flight": 0,
                                                              "at": DAY1 + 3600}})
        self.assertTrue(r.begin())
        self.assertEqual(self.ledger()["holds"][r.hold_id]["in_flight"], 1)

    def test_15_unwritable_ledger_refuses_hold_and_send(self):
        c = self.cap(0.12)
        r = c.reserve()
        os.chmod(self.tmp.name, 0o500)
        try:
            if os.access(self.tmp.name, os.W_OK): self.skipTest("running as root")
            with self.assertLogs("spend_cap", "ERROR"):
                self.assertIsNone(c.reserve())          # its hold could not be persisted
                self.assertFalse(r.begin())             # nor the in-flight mark: do not send
        finally:
            os.chmod(self.tmp.name, 0o700)
        self.assertIsNotNone(c.reserve())               # the refused hold left nothing behind in memory
        self.assertIsNone(c.reserve())

    def test_16_v1_ledger_and_old_orphans(self):
        with open(self.path, "w") as f: json.dump({"day": "2026-10-09", "spent_usd": 0.05}, f)
        c = self.cap(0.12)
        self.assertIsNotNone(c.reserve())               # v1 file read: 0.05 + 0.06 <= 0.12
        self.assertIsNone(c.reserve())
        c.close()
        self.clock.t += 86400                           # next day: yesterday's orphan no longer limits
        c = self.cap(0.12)
        self.assertIsNotNone(c.reserve()); self.assertIsNotNone(c.reserve())
        self.assertEqual(c.orphans(), [])

    def test_17_cost_booked_while_ledger_unreadable_survives_repair(self):
        with open(self.path, "w") as f: f.write("{broken")
        c = self.cap(0.10)
        with self.assertLogs("spend_cap", "ERROR"):
            self.assertEqual(c.settle("unknown", 0.05), "unknown")  # booked in memory, file left for a human
            self.assertIsNone(c.reserve())
        with open(self.path, "w") as f: json.dump({"day": "2026-10-09", "spent_usd": 0.0}, f)
        self.assertIsNone(c.reserve())                  # repaired file + the 0.05 kept: 0.11 > 0.10
        c.settle("unknown", 0.0)                        # first good write carries the kept 0.05
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.05, places=9)

    def test_18_check_spanning_midnight_keeps_its_hold(self):
        self.clock.t = DAY1 + 86400 - 1
        c = self.cap(0.10)
        r = c.reserve(); self.assertTrue(r.begin())
        self.clock.t = DAY1 + 86400 + 1                 # new day, the request is still out
        self.assertIsNone(c.reserve())
        r.record(0.01); r.close()
        self.assertIsNotNone(c.reserve())

    def test_19_tamper_while_running_and_deleted_ledger(self):
        c = self.cap(0.12)
        r = c.reserve()
        with open(self.path, "w") as f: json.dump({"day": "2026-10-09", "spent_usd": 0.0, "holds": {}}, f)
        with self.assertLogs("spend_cap", "ERROR"):
            self.assertIsNone(c.reserve())              # valid JSON, but not what we wrote
        c.close(); os.unlink(self.path)
        c = self.cap(0.12)                              # the lock outlived its ledger: deleted, not new
        with self.assertLogs("spend_cap", "ERROR"):
            self.assertIsNone(c.reserve())

    def test_20_clock_going_back_does_not_reopen_a_spent_day(self):
        self.clock.t = DAY1 + 86400 + 1800              # day 2, 00:30
        c = self.cap(0.12)
        with c.reserve() as r: r.record(0.10)
        self.clock.t = DAY1 + 86400 - 1800              # NTP step back to day 1, 23:30
        with self.assertLogs("spend_cap", "ERROR") as cm:
            self.assertIsNone(c.reserve())              # still day 2's 0.10 + 0.06 > 0.12
        self.assertIn("CLOCK_BACKWARDS", cm.output[0])

    def test_21_closed_cap_fences_its_reservations(self):
        c = self.cap(0.12)
        r = c.reserve(); c.close()
        with self.assertLogs("spend_cap", "ERROR") as cm:
            self.assertFalse(r.begin())                 # no send through a cap that no longer owns the file
            self.assertIsNone(c.reserve())
            r.record(0.01)
        self.assertTrue(all("code=CLOSED" in m for m in cm.output))
        self.assertIn(f"hold={r.hold_id} usd=0.010000", cm.output[-1])
        b = self.cap(0.12)                              # the next owner still sees the hold
        self.assertEqual([o[0] for o in b.orphans()], [r.hold_id])

    def test_22_settle_says_what_it_did(self):
        c = self.cap(0.12)
        r = c.reserve()
        self.assertEqual(c.settle(r.hold_id, 0.05), "live")   # nothing booked: use r.record()
        self.assertEqual(self.ledger()["spent_usd"], 0.0)
        self.assertEqual(c.settle("nope", 0.01), "unknown")   # booked anyway: money spent is money spent
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.01, places=9)

    # -- v2.1: an orphan stops counting when it can no longer bill, not at midnight
    def test_23_orphan_across_midnight_waits_for_its_lifetime(self):
        self.clock.t = DAY1 + 86400 - 60                # A: 23:59, holds 0.06, request out, dies
        hid = self.die_holding("sent")
        self.clock.t = DAY1 + 86400 + 60                # B: 00:01 under a 0.10 cap
        b = self.cap(0.10)
        self.assertIsNone(b.reserve())                  # A's orphan may still bill: 0.06 + 0.06 > 0.10
        self.assertEqual(b.orphans(), [(hid, "2026-10-09", 0.06)])
        self.clock.t = DAY1 + 86400 - 60 + 600          # exactly the lifetime: not yet older than it
        self.assertIsNone(b.reserve())
        self.clock.t += 1                               # 00:09:01, older than 600 s
        with self.assertLogs("spend_cap", "WARNING") as cm:
            self.assertIsNotNone(b.reserve())
        self.assertIn(f"spend_cap_orphan_expired hold={hid} usd=0.060000", cm.output[0])
        self.assertEqual(b.settle(hid, 0.03), "unknown")   # A's late charge still books to today

    def test_24_lifetime_none_means_only_settle_frees(self):
        hid = self.die_holding("sent")
        self.clock.t += 7 * 86400
        b = self.cap(0.10, orphan_lifetime_s=None)
        self.assertIsNone(b.reserve())
        self.assertEqual(b.settle(hid, 0.01), "settled")
        self.assertIsNotNone(b.reserve())
        for bad in (-1, float("nan"), "600", True):
            with self.assertRaises(CapConfigError):
                SpendCap(os.path.join(self.tmp.name, "x.json"), 1.0, orphan_lifetime_s=bad)

    def test_25_v2_hold_without_at_counts_until_its_day_ends_plus_lifetime(self):
        holds = {"old": {"day": "2026-10-09", "usd": 0.06, "in_flight": 1}}
        with open(self.path, "w") as f: json.dump({"day": "2026-10-09", "spent_usd": 0.0, "holds": holds}, f)
        self.clock.t = DAY1 + 86400 + 300               # day 2, 00:05: a v2 hold is taken as 23:59:59.999...
        b = self.cap(0.10)
        self.assertIsNone(b.reserve())
        self.clock.t = DAY1 + 86400 + 601
        self.assertIsNotNone(b.reserve())

    def test_26_at_must_agree_with_day(self):
        for at in (DAY1 - 1, "x", -5, float("inf")):
            holds = {"a": {"day": "2026-10-09", "usd": 0.06, "in_flight": 0, "at": at}}
            with open(self.path, "w") as f: f.write(json.dumps({"day": "2026-10-09", "spent_usd": 0.0, "holds": holds}))
            c = SpendCap(self.path, 2.00, clock=self.clock)
            self.assertEqual((c.state, c.fail_code), ("failed", "LEDGER_CORRUPT"))
            c.close()

    def test_27_state_and_fail_code(self):
        c = self.cap()
        self.assertEqual((c.state, c.fail_code), ("ready", None))
        c.reserve()
        self.assertEqual(c.state, "ready")
        with open(self.path, "w") as f: f.write("{}")
        self.assertEqual(c.fail_code, "LEDGER_CHANGED")
        c.close()
        self.assertEqual((c.state, c.fail_code), ("failed", "CLOSED"))
        os.unlink(self.path)
        b = self.cap()                                  # lock file without its ledger
        self.assertEqual((b.state, b.fail_code), ("failed", "LOCK_WITHOUT_LEDGER"))
        with self.assertLogs("spend_cap", "ERROR") as cm:
            self.assertIsNone(b.reserve())
        self.assertIn("code=LOCK_WITHOUT_LEDGER", cm.output[0])
        b.close(); os.unlink(self.path + ".lock")
        with open(self.path, "w") as f: f.write("{broken")
        d = self.cap()
        self.assertEqual(d.fail_code, "LEDGER_CORRUPT")
        self.clock.t = DAY1 + 2 * 86400; os.unlink(self.path)
        d.close(); os.unlink(self.path + ".lock")
        e = self.cap(); e.reserve()
        self.clock.t = DAY1                             # clock stepped back past the ledger's day
        self.assertEqual(e.fail_code, "CLOCK_BACKWARDS")

    def test_28_hold_taken_as_midnight_ticks_is_readable_by_the_next_owner(self):
        ticks = iter(DAY1 + 86400 - 1 + 0.5 * i for i in range(100))   # every clock read moves time on
        c = SpendCap(self.path, 0.10, clock=lambda: next(ticks))
        self.assertIsNotNone(c.reserve()); c.close()
        b = self.cap(0.10)
        self.assertEqual(b.fail_code, None)             # "at" and "day" were taken from one reading

    def test_29_v2_hold_survives_a_write_and_a_restart(self):   # zenith 80987, written by looktwin 80995
        holds = {"old": {"day": "2026-10-09", "usd": 0.06, "in_flight": 1}}
        with open(self.path, "w") as f: json.dump({"day": "2026-10-09", "spent_usd": 0.0, "holds": holds}, f)
        self.clock.t = DAY1 + 86400 + 300
        b = SpendCap(self.path, 2.00, clock=self.clock)
        r = b.reserve(); self.assertIsNotNone(r); r.close(); b.close()
        self.assertNotIn("at", self.ledger()["holds"]["old"])   # written back as the v2 owner wrote it
        c = SpendCap(self.path, 2.00, clock=self.clock)
        self.assertEqual((c.state, c.fail_code), ("ready", None))
        self.assertIn("old", [o[0] for o in c.orphans()])
        c.close()

    def test_30_todays_orphan_counts_all_day_whatever_its_age(self):
        a = SpendCap(self.path, 0.10, clock=self.clock)
        r = a.reserve(); self.assertTrue(r.begin()); a.close()   # dies with a request in flight
        self.clock.t = DAY1 + 23 * 3600                  # the same day, 22 h later: far past the lifetime
        b = self.cap(0.10)
        self.assertEqual(len(b.orphans()), 1)
        self.assertIsNone(b.reserve())                   # its money is not back in today's budget


if __name__ == "__main__":
    unittest.main()
