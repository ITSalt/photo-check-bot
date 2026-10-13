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

    def books(self):
        """The ledger without the settle-once memory (WP-CONTROLS-06 added the "memo" key)."""
        led = self.ledger(); led.pop("memo"); return led


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
        self.assertEqual(self.books(), {"day": "2026-10-10", "spent_usd": 0.01, "holds": {}})
        self.assertEqual([(m["k"], m["usd"], m["at"]) for m in self.ledger()["memo"].values()],
                         [("released", 0.0, DAY1 + 3600)] * 2 + [("released", 0.0, DAY1 + 86400 + 1)])

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
        self.assertEqual(self.ledger(), {"day": "2026-10-09", "spent_usd": 0.0123, "holds": {}, "memo": {
            r.hold_id: {"k": "released", "usd": 0.0, "at": DAY1 + 3600}}})
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
        self.assertEqual(self.ledger(), {"day": "2026-10-09", "spent_usd": 0.01, "holds": {}, "memo": {
            r.hold_id: {"k": "released", "usd": 0.0, "at": DAY1 + 3600}}})
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
        self.assertEqual(b.settle(hid, 0.03), "expired")   # A's late charge still books to today

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


class SettleOnce(Base):
    """WP-CONTROLS-06: a hold is settled once. The ledger remembers released, expired and settled ids
    under "memo" (rule: theone, board task S1; the pure model is tests/settle_once_model.py)."""
    DAYS14 = 1209600

    def orphan(self, cap_usd=2.00):
        """A hold whose owner died with a request in flight; returns its id."""
        a = SpendCap(self.path, cap_usd, clock=self.clock)
        r = a.reserve(); self.assertTrue(r.begin()); a.close()
        return r.hold_id

    def spent(self):
        return self.ledger()["spent_usd"]

    def test_31_second_settle_of_an_unknown_id_is_a_repeat(self):   # test_22, continued
        c = self.cap(0.12)
        self.assertEqual(c.settle("nope", 0.01), "unknown")
        self.assertEqual(c.settle("nope", 0.01), "repeat")    # the same charge is not booked twice
        self.assertAlmostEqual(self.spent(), 0.01, places=9)
        self.assertEqual(self.ledger()["memo"], {"nope": {"k": "settled", "usd": 0.01, "at": DAY1 + 3600}})

    def test_32_three_settles_of_one_orphan_book_once(self):
        hid = self.orphan(0.10)
        b = self.cap(0.10)
        self.assertEqual([b.settle(hid, 0.01) for _ in range(3)], ["settled", "repeat", "repeat"])
        self.assertAlmostEqual(self.spent(), 0.01, places=9)
        self.assertEqual(b.orphans(), [])
        self.assertIsNotNone(b.reserve())               # 0.01 + 0.06 <= 0.10
        self.assertIsNone(b.reserve())

    def test_33_settle_after_a_normal_close_books_nothing(self):
        c = self.cap(0.12)
        r = c.reserve(); self.assertTrue(r.begin()); r.record(0.004); r.close()
        self.assertEqual(c.settle(r.hold_id, 0.05), "released")
        self.assertEqual(c.settle(r.hold_id, 0.05), "released")
        self.assertAlmostEqual(self.spent(), 0.004, places=9)
        self.assertEqual(self.ledger()["memo"], {r.hold_id: {"k": "released", "usd": 0.0, "at": DAY1 + 3600}})

    def test_34_expired_orphan_books_once_then_repeat(self):
        self.clock.t = DAY1 + 86400 - 60
        hid = self.orphan(0.10)
        self.clock.t = DAY1 + 86400 + 600               # the next day, older than the 600 s lifetime
        b = self.cap(0.10)
        with self.assertLogs("spend_cap", "WARNING"):
            self.assertIsNotNone(b.reserve())           # the orphan expired: it no longer counts
        self.assertEqual(self.ledger()["memo"], {hid: {"k": "expired", "usd": 0.0, "at": DAY1 + 86400 + 600}})
        self.assertEqual(b.settle(hid, 0.03), "expired")   # its late charge books to today, once
        self.assertEqual(b.settle(hid, 0.03), "repeat")
        self.assertAlmostEqual(self.spent(), 0.03, places=9)
        self.assertEqual(self.ledger()["memo"][hid]["k"], "settled")

    def test_35_repeat_books_only_what_a_higher_cost_adds(self):
        hid = self.orphan()
        b = self.cap()
        self.assertEqual(b.settle(hid, 0.01), "settled")
        self.assertEqual(b.settle(hid, 0.03), "repeat")    # typed too low before: the difference is booked
        self.assertAlmostEqual(self.spent(), 0.03, places=9)
        self.assertEqual(b.settle(hid, 0.02), "repeat")    # a lower cost takes nothing back
        self.assertEqual(b.settle(hid, None), "repeat")    # a bad cost is the fallback 0.02: not above 0.03
        self.assertAlmostEqual(self.spent(), 0.03, places=9)
        self.assertEqual(self.ledger()["memo"][hid]["usd"], 0.03)   # the highest, not the last
        self.assertEqual(b.settle(hid, 0.035), "repeat")
        self.assertAlmostEqual(self.spent(), 0.035, places=9)
        self.assertEqual(b.settle("other", "abc"), "unknown")       # a bad cost on a new id: the fallback
        self.assertAlmostEqual(self.spent(), 0.055, places=9)

    def test_36_memory_survives_a_restart(self):
        hid = self.orphan()
        b = SpendCap(self.path, 2.00, clock=self.clock)
        r = b.reserve(); r.close()
        self.assertEqual(b.settle(hid, 0.01), "settled")
        b.close()
        self.clock.t += 5 * 86400                       # days later, another process
        c = self.cap()
        self.assertEqual((c.state, c.fail_code), ("ready", None))
        self.assertEqual(c.settle(hid, 0.01), "repeat")
        self.assertEqual(c.settle(r.hold_id, 0.01), "released")
        self.assertEqual(self.spent(), 0.0)             # a new day, and nothing booked to it
        self.assertEqual(self.ledger()["memo"], {
            r.hold_id: {"k": "released", "usd": 0.0, "at": DAY1 + 3600},
            hid: {"k": "settled", "usd": 0.01, "at": DAY1 + 3600 + 5 * 86400}})

    def test_37_forgotten_after_14_days_is_unknown_again(self):
        c = self.cap()
        r = c.reserve(); r.close()                      # released at t0
        self.assertEqual(c.settle("x", 0.01), "unknown")   # settled at t0
        self.clock.t += self.DAYS14 - 1
        self.assertEqual(c.settle("x", 0.01), "repeat")    # still remembered, and remembered afresh from now
        self.assertEqual(c.settle(r.hold_id, 0.01), "released")   # a released id is not remembered afresh
        self.clock.t += 1                               # 14 days after the release
        self.assertEqual(c.settle(r.hold_id, 0.01), "unknown")    # forgotten: booked in full, the safe side
        self.assertAlmostEqual(self.spent(), 0.01, places=9)
        self.assertEqual(c.settle("x", 0.01), "repeat")
        self.clock.t += self.DAYS14 - 1
        self.assertEqual(sorted(self.ledger()["memo"]), sorted(["x", r.hold_id]))
        self.clock.t += 1
        self.assertEqual(c.settle("x", 0.01), "unknown")
        self.assertEqual(list(self.ledger()["memo"]), ["x"])   # the other one aged out in the same step
        short = SpendCap(os.path.join(self.tmp.name, "s.json"), 1.0, clock=self.clock, settle_memory_s=60)
        self.addCleanup(short.close)
        self.assertEqual(short.settle("y", 0.01), "unknown")
        self.clock.t += 59
        self.assertEqual(short.settle("y", 0.01), "repeat")
        self.clock.t += 60
        self.assertEqual(short.settle("y", 0.01), "unknown")

    def test_38_memory_max_forgets_the_oldest(self):
        c = self.cap(settle_memory_max=2)
        for hid in ("a", "b", "c"):
            self.assertEqual(c.settle(hid, 0.01), "unknown")
            self.clock.t += 1
        self.assertEqual(sorted(self.ledger()["memo"]), ["b", "c"])   # the oldest went, the newest stayed
        self.assertEqual(c.settle("c", 0.01), "repeat")
        self.assertEqual(c.settle("a", 0.01), "unknown")              # forgotten: booked in full again
        self.assertEqual(sorted(self.ledger()["memo"]), ["a", "c"])
        self.assertAlmostEqual(self.spent(), 0.04, places=9)
        tie = SpendCap(os.path.join(self.tmp.name, "tie.json"), 1.0, clock=self.clock, settle_memory_max=2)
        self.addCleanup(tie.close)
        for hid in ("b", "a", "c"):                     # all in the same second
            tie.settle(hid, 0.01)
        with open(os.path.join(self.tmp.name, "tie.json")) as f:
            self.assertEqual(sorted(json.load(f)["memo"]), ["b", "c"])   # equally old: the smaller id goes
        none = SpendCap(os.path.join(self.tmp.name, "none.json"), 1.0, clock=self.clock, settle_memory_max=0)
        self.addCleanup(none.close)
        self.assertEqual([none.settle("z", 0.01) for _ in range(2)], ["unknown", "unknown"])   # nothing kept
        for kw in ({"settle_memory_s": -1}, {"settle_memory_s": float("nan")}, {"settle_memory_s": None},
                   {"settle_memory_s": "60"}, {"settle_memory_s": True}, {"settle_memory_max": -1},
                   {"settle_memory_max": 2.5}, {"settle_memory_max": True}, {"settle_memory_max": None}):
            with self.assertRaises(CapConfigError):
                SpendCap(os.path.join(self.tmp.name, "x.json"), 1.0, **kw)

    def test_39_malformed_memo_is_corrupt(self):
        ok = {"k": "settled", "usd": 0.01, "at": DAY1}
        for memo in ([], "x", None, 0,
                     {"a": []}, {"a": "settled"}, {"a": None},
                     {"a": {"k": "settled", "usd": 0.01}}, {"a": {"k": "settled", "at": DAY1}},
                     {"a": {"usd": 0.01, "at": DAY1}}, {"a": dict(ok, day="2026-10-09")},
                     {"a": dict(ok, k="live")}, {"a": dict(ok, k="")}, {"a": dict(ok, k=None)},
                     {"a": dict(ok, k=["settled"])}, {"a": dict(ok, k=1)},
                     {"a": dict(ok, usd=-0.01)}, {"a": dict(ok, usd="0.01")}, {"a": dict(ok, usd=None)},
                     {"a": dict(ok, usd=True)}, {"a": dict(ok, usd=float("inf"))}, {"a": dict(ok, usd=float("nan"))},
                     {"a": dict(ok, usd=10 ** 400)},
                     {"a": dict(ok, at="yesterday")}, {"a": dict(ok, at=None)}, {"a": dict(ok, at=True)},
                     {"a": dict(ok, at=float("inf"))}, {"a": dict(ok, at=float("nan"))}, {"a": dict(ok, at=10 ** 400)},
                     {"good": ok, "bad": dict(ok, k="gone")}):
            with open(self.path, "w") as f:
                json.dump({"day": "2026-10-09", "spent_usd": 0.0, "holds": {}, "memo": memo}, f)
            c = SpendCap(self.path, 2.00, clock=self.clock)
            self.assertEqual((c.state, c.fail_code), ("failed", "LEDGER_CORRUPT"), memo)
            with self.assertLogs("spend_cap", "ERROR") as cm:
                self.assertIsNone(c.reserve())
            self.assertEqual(cm.records[0].getMessage(), "spend_cap_error code=LEDGER_CORRUPT")
            c.close()
        for data in ({"day": "2026-10-09", "spent_usd": 0.0, "memo": {}},              # never written so
                     {"day": "2026-10-09", "spent_usd": 0.0, "holds": {}, "memo": {}, "x": 1},
                     {"day": "2026-10-09", "spent_usd": 0.0, "holds": {}, "memory": {}}):
            with open(self.path, "w") as f: json.dump(data, f)
            c = SpendCap(self.path, 2.00, clock=self.clock)
            self.assertEqual(c.fail_code, "LEDGER_CORRUPT", data)
            c.close()
        for memo in ({}, {"a": ok}, {"a": dict(ok, k="released", usd=0)}, {"a": dict(ok, k="expired", at=-5)}):
            with open(self.path, "w") as f:
                json.dump({"day": "2026-10-09", "spent_usd": 0.0, "holds": {}, "memo": memo}, f)
            c = SpendCap(self.path, 2.00, clock=self.clock)
            self.assertEqual(c.fail_code, None, memo)
            c.close()

    def test_40_older_ledgers_load_with_nothing_remembered(self):
        holds = {"old": {"day": "2026-10-09", "usd": 0.06, "in_flight": 1, "at": DAY1 + 60},
                 "v2": {"day": "2026-10-09", "usd": 0.06, "in_flight": 0}}
        for old in ({"day": "2026-10-09", "spent_usd": 0.05},                      # v1
                    {"day": "2026-10-09", "spent_usd": 0.05, "holds": {}},          # v2.1.1, idle
                    {"day": "2026-10-09", "spent_usd": 0.05, "holds": holds}):      # v2.1.1 and v2 holds
            with open(self.path, "w") as f: json.dump(old, f)
            c = SpendCap(self.path, 2.00, clock=self.clock)
            self.assertEqual((c.state, c.fail_code), ("ready", None))
            self.assertEqual(self.ledger(), old)            # reading alone does not rewrite the file
            self.assertEqual(sorted(o[0] for o in c.orphans()), sorted(old.get("holds", {})))
            r = c.reserve(); self.assertIsNotNone(r)        # no manual step: the first write adds the key
            self.assertEqual(self.ledger()["memo"], {})
            self.assertEqual(self.ledger()["spent_usd"], 0.05)
            self.assertEqual(sorted(self.ledger()["holds"]), sorted(list(old.get("holds", {})) + [r.hold_id]))
            self.assertEqual(c.settle("before-the-upgrade", 0.01), "unknown")   # every old id is unknown
            self.assertAlmostEqual(self.spent(), 0.06, places=9)
            c.close()

    def test_41_check_cycle_with_2000_remembered_ids_is_fast(self):
        memo = {"%032x" % i: {"k": ("released", "settled", "expired")[i % 3], "usd": 0.004 * (i % 3 == 1),
                              "at": DAY1 + i} for i in range(2000)}
        with open(self.path, "w") as f:
            json.dump({"day": "2026-10-09", "spent_usd": 0.0, "holds": {}, "memo": memo}, f)
        c = self.cap()
        one_call, check = [], []
        for _ in range(3):                              # the best of three: a busy disk only adds time
            t0 = time.perf_counter()
            r = c.reserve(); self.assertTrue(r.begin()); r.record(0.004); r.close()
            one_call.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            r = c.reserve()
            for _ in range(3):                          # a whole check: three provider calls, 8 ledger writes
                self.assertTrue(r.begin()); r.record(0.004)
            r.close()
            check.append(time.perf_counter() - t0)
        self.assertEqual(len(self.ledger()["memo"]), 2000)   # full: the six oldest made room for these six
        self.assertEqual(self.ledger()["memo"][r.hold_id]["k"], "released")
        self.assertNotIn("%032x" % 5, self.ledger()["memo"])
        self.assertIn("%032x" % 6, self.ledger()["memo"])
        print("spend cap, 2000 remembered ids, ledger %d bytes: reserve/begin/record/close %.4f s (worst of 3: "
              "%.4f), a 3-call check %.4f s (worst of 3: %.4f)"
              % (os.path.getsize(self.path), min(one_call), max(one_call), min(check), max(check)), file=sys.stderr)
        self.assertLess(min(one_call), 0.5)
        self.assertLess(min(check), 0.5)

    def test_42_settle_while_the_ledger_is_unreadable_is_still_once(self):
        with open(self.path, "w") as f: f.write("{broken")
        c = self.cap(0.10)
        with self.assertLogs("spend_cap", "ERROR"):
            self.assertEqual(c.settle("x", 0.05), "unknown")   # booked and remembered in memory only
            self.assertEqual(c.settle("x", 0.05), "repeat")
            self.assertEqual(c.settle("y", 0.01), "unknown")   # the ledger knows better, but cannot be read
        with open(self.path, "w") as f:
            json.dump({"day": "2026-10-09", "spent_usd": 0.0, "holds": {}, "memo": {
                "y": {"k": "released", "usd": 0.0, "at": DAY1}}}, f)
        self.assertEqual(c.settle("x", 0.05), "repeat")        # the repaired ledger gets the costs and x's id
        self.assertEqual(c.settle("y", 0.05), "released")      # and what it remembered itself of an id stands
        self.assertAlmostEqual(self.spent(), 0.06, places=9)   # y's 0.01 stays booked: counted too much, not too little
        self.assertEqual(self.ledger()["memo"], {"y": {"k": "released", "usd": 0.0, "at": DAY1},
                                                 "x": {"k": "settled", "usd": 0.05, "at": DAY1 + 3600}})

    def test_43_only_a_normal_close_is_remembered_as_released(self):
        c = self.cap(0.20)
        live = c.reserve()
        self.assertEqual(c.settle(live.hold_id, 0.05), "live")
        self.assertEqual(self.ledger()["memo"], {})         # a live hold is not remembered
        r = c.reserve(); self.assertTrue(r.begin()); r.close()   # closed with a request out: an orphan
        self.assertEqual(self.ledger()["memo"], {})         # ... and an orphan is not "released"
        self.assertEqual(c.settle(r.hold_id, 0.01), "settled")
        self.assertEqual(c.settle(r.hold_id, 0.01), "repeat")
        r2 = c.reserve(); self.assertTrue(r2.begin()); r2.close()
        r2.record(0.004)                                    # the late answer frees the orphan by itself
        self.assertNotIn(r2.hold_id, self.ledger()["memo"])
        self.assertEqual(c.settle(r2.hold_id, 0.004), "unknown")   # as before: booked, the safe side
        self.assertAlmostEqual(self.spent(), 0.018, places=9)

    def test_44_orphan_hold_that_is_also_remembered_released_is_settled(self):   # P1, rosenrot
        hid = "h" * 32                                      # a hand-merged ledger: the id is both
        with open(self.path, "w") as f:
            json.dump({"day": "2026-10-09", "spent_usd": 0.0,
                       "holds": {hid: {"day": "2026-10-09", "usd": 0.06, "in_flight": 1, "at": DAY1 + 60}},
                       "memo": {hid: {"k": "released", "usd": 0.0, "at": DAY1 + 120}}}, f)
        c = self.cap(0.12)
        self.assertEqual(c.orphans(), [(hid, "2026-10-09", 0.06)])
        self.assertEqual(c.settle(hid, 0.05), "settled")    # the hold wins over the memory
        self.assertAlmostEqual(self.spent(), 0.05, places=9)
        self.assertEqual(self.ledger()["holds"], {})
        self.assertEqual(self.ledger()["memo"], {hid: {"k": "settled", "usd": 0.05, "at": DAY1 + 3600}})
        self.assertEqual(c.settle(hid, 0.05), "repeat")     # and from then on it is settled once
        self.assertAlmostEqual(self.spent(), 0.05, places=9)

    def test_45_ledger_over_memory_max_is_trimmed_at_the_first_roll(self):   # P2, rosenrot
        memo = {hid: {"k": "settled", "usd": 0.01, "at": DAY1 + i} for i, hid in enumerate("abcd")}
        with open(self.path, "w") as f:
            json.dump({"day": "2026-10-09", "spent_usd": 0.04, "holds": {}, "memo": memo}, f)
        c = self.cap(settle_memory_max=2)
        self.assertEqual(c.fail_code, None)                 # reading does not trim and does not write
        self.assertEqual(sorted(self.ledger()["memo"]), ["a", "b", "c", "d"])
        self.assertIsNotNone(c.reserve())                   # remembers nothing itself: only _roll trims
        self.assertEqual(sorted(self.ledger()["memo"]), ["c", "d"])   # the oldest two went first
        self.assertEqual(c.settle("a", 0.01), "unknown")    # forgotten: booked in full again
        self.assertEqual(c.settle("d", 0.01), "repeat")


    def test_46_corrupt_memo_reads_leave_exact_bytes(self):   # P3, theone
        # Valid JSON, invalid memo kind. Noncanonical bytes expose even a reformat-only rewrite.
        raw = (b'{  "day" : "2026-10-09", "spent_usd" : 0.000, "holds" : {},\n'
               b'   "memo" : {"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" : '
               b'{"k" : "broken", "usd" : 0.010, "at" : 1791504000}} }\n')
        with open(self.path, "wb") as f:
            f.write(raw)
        c = self.cap()
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), raw)                 # constructing a reader cannot repair it
        for field, expected in (("fail_code", "LEDGER_CORRUPT"), ("state", "failed"),
                                ("fail_code", "LEDGER_CORRUPT"), ("state", "failed")):
            with self.subTest(field=field):
                self.assertEqual(getattr(c, field), expected)
                with open(self.path, "rb") as f:
                    self.assertEqual(f.read(), raw)         # every observation leaves original evidence
        c.close()
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), raw)

    def test_47_each_settle_prefix_books_the_maximum_not_the_sum(self):   # nadir-codex control, theone patch
        # One remembered id, one day, no other charges or expiry. This is not a lifetime cap.
        hid = "a" * 32
        c = self.cap()
        high = 0
        for i, amount in enumerate((30000, 10000, 30000, 50000, 20000, 0, 70000, 70000)):
            with self.subTest(step=i, amount_micro=amount):
                self.assertEqual(c.settle(hid, amount / 1000000), "unknown" if i == 0 else "repeat")
                high = max(high, amount)
                self.assertEqual(c._spent, high)
                self.assertEqual(c._memo[hid].usd, high)
                ledger = self.ledger()
                self.assertAlmostEqual(ledger["spent_usd"], high / 1000000, places=9)
                self.assertEqual(ledger["memo"][hid]["k"], "settled")
                self.assertAlmostEqual(ledger["memo"][hid]["usd"], high / 1000000, places=9)
                self.assertEqual(ledger["holds"], {})
                if i == 3:
                    c.close()
                    c = self.cap()  # same clock and persisted memo; lower claim comes first after restart


if __name__ == "__main__":
    unittest.main()
