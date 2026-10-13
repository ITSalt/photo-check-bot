"""WP-CONTROLS-07 (PR A of settle-once, D-17): the ledger loader accepts a top-level "memo" object,
writes it back unchanged with every write and never uses it. Everything else is as before: a "memo"
that is not an object and every other unknown key is LEDGER_CORRUPT, and a ledger without "memo" is
written without it, byte for byte as main 18d6caa writes it.

The comparison with main reads app/spend_cap.py of 18d6caa from this repository's git objects; where
they are not there (a shallow CI checkout) that one test is skipped and says so. More traces:
SPEND_CAP_PROPERTY_TRACES=2000 python3 -m unittest tests.test_spend_cap_memo_kept"""
import hashlib, json, logging, os, random, subprocess, tempfile, types, unittest, uuid
from unittest import mock

from app import spend_cap as head

DAY1 = 1791504000.0          # 2026-10-09 00:00:00 UTC
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_SHA = "18d6caa3a24ed13ed4932dc396b5750d60de6776"
MAIN_SPEND_CAP_SHA256 = "20218a656e168c7c72ffd0087b171eacde0bf77cb943be929846fc2bb7f993db"
TRACES = int(os.environ.get("SPEND_CAP_PROPERTY_TRACES", "15"))
STEPS = 40
MEMO = {"3f" * 16: {"k": "released", "usd": 0.0, "at": DAY1 + 60},
        "a1" * 16: {"k": "settled", "usd": 0.0123, "at": DAY1 - 86400.5},
        "b2" * 16: {"k": "expired", "usd": 0, "at": 1791500000},
        "not an entry at all": [1, 2.5, None, True, {"нет": "ж", "big": 12345678901234567890123}]}


def main_module():
    """app/spend_cap.py as it is on main 18d6caa, or None when the git objects are not here."""
    try:
        p = subprocess.run(["git", "-C", ROOT, "show", MAIN_SHA + ":app/spend_cap.py"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0 or hashlib.sha256(p.stdout).hexdigest() != MAIN_SPEND_CAP_SHA256:
        return None
    m = types.ModuleType("spend_cap_main_18d6caa")
    exec(compile(p.stdout, "spend_cap@18d6caa", "exec"), m.__dict__)
    return m


def start_ledger(rng, memo=None):
    """A ledger a previous owner could have left: none, v1, v2 (holds without "at") or v2.1.1."""
    kind = "v211" if memo is not None else rng.choice(["none", "v1", "v2", "v211", "v211"])
    if kind == "none":
        return None
    day_t = DAY1 - rng.choice([0, 0, 86400])
    day = head._utc_day(day_t)
    data = {"day": day, "spent_usd": rng.choice([0.0, 0.004, 0.05, 0.1005])}
    if kind != "v1":
        data["holds"] = {}
        for i in range(rng.randrange(0, 3)):
            h = {"day": day, "usd": rng.choice([0.06, 0.056, 0.02]), "in_flight": rng.randrange(0, 2)}
            if kind == "v211" and rng.random() < 0.8:
                h["at"] = day_t + rng.choice([10, 3000, 86000])
            data["holds"]["old%d" % i] = h
    if memo is not None:
        data["memo"] = memo
    return json.dumps(data).encode("utf-8")


def run_trace(mod, seed, start, steps=STEPS):
    """Random operations on a SpendCap of `mod` over the ledger `start`, with a fake clock and fake
    hold ids. Returns [(operation, what it returned, the ledger file's bytes after it)]."""
    rng = random.Random(seed)
    ids = iter(range(1, 10 ** 6))
    out, now = [], [DAY1 + 3600]
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(uuid, "uuid4", lambda: types.SimpleNamespace(hex="%032x" % next(ids))):
        path = os.path.join(d, "spend_cap.json")
        if start is not None:
            with open(path, "wb") as f:
                f.write(start)
        new = lambda: mod.SpendCap(path, 0.20, clock=lambda: now[0], orphan_lifetime_s=rng.choice([600, 600, None]))
        cap, res = new(), []

        def seen(op, result):
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except FileNotFoundError:
                raw = None
            out.append((op, result, raw))

        seen("open", cap.fail_code)
        for _ in range(steps):
            op = rng.choice(["reserve", "reserve", "begin", "record", "record", "not_sent", "close", "settle",
                             "settle", "tick", "restart", "orphans", "state"])
            r = rng.choice(res) if res else None
            if op == "reserve":
                got = cap.reserve()
                if got is not None:
                    res.append(got)
                result = None if got is None else got.hold_id
            elif op in ("begin", "record", "not_sent", "close") and r is None:
                continue
            elif op == "begin":
                result = r.begin()
            elif op == "record":
                if rng.random() < 0.7:
                    r.begin()
                result = r.record(rng.choice([0.004, 0.0123, 0.02, 0.07, None, float("nan"), -1]))
            elif op == "not_sent":
                result = r.not_sent()
            elif op == "close":
                result = r.close()
            elif op == "settle":
                known = [o[0] for o in cap.orphans()] + [x.hold_id for x in res] + ["old0", "nope", "3f" * 16]
                result = cap.settle(rng.choice(known), rng.choice([0.0, 0.005, 0.03, "bad"]))
            elif op == "tick":
                now[0] += rng.choice([1, 30, 599, 601, 3600, 40000, 86400])
                result = None
            elif op == "restart":
                cap.close()
                cap, res = new(), [x for x in res if rng.random() < 0.3]   # some old handles stay around
                result = cap.orphans()
            elif op == "orphans":
                result = cap.orphans()
            else:
                result = (cap.state, cap.fail_code)
            seen(op, result)
        cap.close()
    return out


class Quiet(unittest.TestCase):
    def setUp(self):
        logging.getLogger("spend_cap").disabled = True
        self.addCleanup(setattr, logging.getLogger("spend_cap"), "disabled", False)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "ledger.json")
        self.now = DAY1 + 3600

    def cap(self, cap_usd=2.00, **kw):
        c = head.SpendCap(self.path, cap_usd, clock=lambda: self.now, **kw)
        self.addCleanup(c.close)
        return c

    def put(self, **data):
        with open(self.path, "w") as f: json.dump(data, f)

    def raw(self):
        with open(self.path, "rb") as f: return f.read()

    def ledger(self):
        return json.loads(self.raw())


class MemoKept(Base):
    def test_1_ledger_with_a_memo_object_loads(self):
        for memo in ({}, MEMO):
            self.put(day="2026-10-09", spent_usd=0.05, holds={}, memo=memo)
            c = head.SpendCap(self.path, 0.12, clock=lambda: self.now)
            self.assertEqual((c.state, c.fail_code), ("ready", None))
            self.assertIsNotNone(c.reserve())               # 0.05 + 0.06 <= 0.12: the total was read
            self.assertIsNone(c.reserve())
            c.close()

    def test_2_every_write_keeps_the_memo_and_a_restart_reads_it(self):
        holds = {"dead": {"day": "2026-10-09", "usd": 0.06, "in_flight": 1, "at": DAY1 + 60}}
        self.put(day="2026-10-09", spent_usd=0.0, holds=holds, memo=MEMO)
        memo_bytes = json.dumps(MEMO).encode("utf-8")
        writes = []

        def kept():
            self.assertEqual(self.ledger()["memo"], MEMO)
            self.assertEqual(list(self.ledger()), ["day", "spent_usd", "holds", "memo"])
            self.assertIn(b'"memo": ' + memo_bytes + b"}", self.raw())   # the same JSON text, not only equal
            writes.append(self.raw())

        c = self.cap()
        r = c.reserve(); kept()
        self.assertTrue(r.begin()); kept()
        r.record(0.0123); kept()
        self.assertTrue(r.begin()); kept()
        r.not_sent(); kept()                                 # the same ledger as before that begin()
        r.close(); kept()
        self.assertEqual(c.settle("dead", 0.005), "settled"); kept()
        self.assertEqual(c.settle("nope", 0.01), "unknown"); kept()
        self.assertEqual((len(writes), len(set(writes))), (8, 7))   # each step did rewrite the file
        self.now += 86400                                    # the day rolls: the memo is not the day's
        with c.reserve() as r2: r2.record(0.001)
        kept()
        self.assertEqual({k: v for k, v in self.ledger().items() if k != "memo"},
                         {"day": "2026-10-10", "spent_usd": 0.001, "holds": {}})
        c.close()
        b = self.cap()                                       # a restart still reads it
        self.assertEqual((b.state, b.fail_code), ("ready", None))
        with b.reserve() as r3: r3.record(0.002)
        kept()
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.003, places=9)

    def test_3_memo_that_is_not_an_object_is_corrupt(self):
        for memo in ([], [{"k": "settled"}], "x", "", None, 0, 1.5, True):
            self.put(day="2026-10-09", spent_usd=0.0, holds={}, memo=memo)
            before = self.raw()
            c = head.SpendCap(self.path, 2.00, clock=lambda: self.now)
            self.assertEqual((c.state, c.fail_code), ("failed", "LEDGER_CORRUPT"), memo)
            with self.assertLogs("spend_cap", "ERROR") as cm:
                self.assertIsNone(c.reserve())
            self.assertEqual(cm.records[0].getMessage(), "spend_cap_error code=LEDGER_CORRUPT")
            c.close()
            self.assertEqual(self.raw(), before)             # left untouched for a human to look at

    def test_4_every_other_unknown_key_is_still_corrupt(self):
        ok = {"day": "2026-10-09", "spent_usd": 0.0, "holds": {}}
        for extra in ({"memo2": {}}, {"Memo": {}}, {"x": 1}, {"memo": {}, "x": 1}, {"memo": {}, "memory": {}},
                      {"version": 3, "memo": MEMO}):
            self.put(**ok, **extra)
            c = head.SpendCap(self.path, 2.00, clock=lambda: self.now)
            self.assertEqual((c.state, c.fail_code), ("failed", "LEDGER_CORRUPT"), extra)
            c.close()
        self.put(day="2026-10-09", spent_usd=0.0, memo={})   # no revision writes "memo" without "holds"
        c = head.SpendCap(self.path, 2.00, clock=lambda: self.now)
        self.assertEqual(c.fail_code, "LEDGER_CORRUPT")
        c.close()

    def test_5_ledger_without_memo_is_written_without_it(self):
        starts = (None, {"day": "2026-10-09", "spent_usd": 0.05},
                  {"day": "2026-10-09", "spent_usd": 0.05, "holds": {}})
        for start in starts:
            for p in (self.path, self.path + ".lock"):
                os.path.exists(p) and os.unlink(p)
            if start is not None:
                self.put(**start)
            c = head.SpendCap(self.path, 2.00, clock=lambda: self.now)
            with c.reserve() as r:
                self.assertTrue(r.begin()); r.record(0.0123)
            c.close()
            spent = 0.0123 + (start or {}).get("spent_usd", 0)
            self.assertEqual(self.raw(), json.dumps({"day": "2026-10-09", "spent_usd": round(spent * 1e6) / 1e6,
                                                     "holds": {}}).encode("utf-8"))

    def test_6_the_memo_is_not_used(self):
        """Ids that the memo names are settled, expired and released exactly as on a ledger without it."""
        holds = {"dead": {"day": "2026-10-09", "usd": 0.06, "in_flight": 1, "at": DAY1 + 60}}
        memo = {"dead": {"k": "released", "usd": 0.0, "at": DAY1}, "gone": {"k": "settled", "usd": 9.0, "at": DAY1},
                "late": {"k": "expired", "usd": 0.0, "at": DAY1}}
        self.put(day="2026-10-09", spent_usd=0.0, holds=holds, memo=memo)
        c = self.cap(0.10)
        self.assertEqual(c.orphans(), [("dead", "2026-10-09", 0.06)])
        self.assertIsNone(c.reserve())                       # the orphan still counts in full
        self.assertEqual(c.settle("dead", 0.01), "settled")  # booked, although the memo calls it released
        self.assertEqual(c.settle("gone", 0.01), "unknown")  # booked again, as today
        self.assertEqual(c.settle("late", 0.01), "unknown")
        self.assertEqual(c.settle("dead", 0.01), "unknown")
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.04, places=9)
        self.assertEqual(self.ledger()["memo"], memo)        # and nothing was added to it or aged out of it
        self.now += 400 * 86400
        with c.reserve() as r: r.record(0.001)
        self.assertEqual(self.ledger()["memo"], memo)


class Properties(Quiet):
    def test_7_a_memo_changes_nothing_but_its_own_key(self):
        """The same operations on the same ledger with and without a memo: the same answers and the same
        file, apart from the "memo" key, which stays exactly as it was."""
        for seed in range(TRACES):
            rng = random.Random(seed)
            with_memo = start_ledger(rng, memo=rng.choice([{}, MEMO]))
            data = json.loads(with_memo)
            memo = data.pop("memo")
            a, b = run_trace(head, seed, json.dumps(data).encode("utf-8")), run_trace(head, seed, with_memo)
            self.assertEqual(len(a), len(b), seed)
            for (op, got, raw), (op_m, got_m, raw_m) in zip(a, b):
                self.assertEqual((op, got), (op_m, got_m), seed)
                led_m = json.loads(raw_m)
                self.assertEqual(led_m.pop("memo"), memo, seed)
                self.assertEqual(json.dumps(led_m).encode("utf-8"), raw, seed)

    def test_8_without_a_memo_this_head_writes_what_main_writes(self):
        """For ledgers without "memo" this head and main 18d6caa give the same answers and write
        byte-identical files after every operation."""
        main = main_module()
        if main is None:
            self.skipTest("app/spend_cap.py of main 18d6caa is not in this checkout's git objects")
        ops = files = 0
        for seed in range(TRACES):
            start = start_ledger(random.Random(seed))
            a, b = run_trace(main, seed, start), run_trace(head, seed, start)
            self.assertEqual(a, b, seed)
            ops += len(a); files += len({raw for _, _, raw in a})
            self.assertFalse(any(raw is not None and b'"memo"' in raw for _, _, raw in b), seed)
        self.assertGreater(files, 5 * TRACES)                # the traces did write: not a comparison of nothing
        if os.environ.get("SPEND_CAP_PROPERTY_TRACES"):
            print("\nhead vs main 18d6caa: %d traces, %d operations, %d distinct ledger files, all identical"
                  % (TRACES, ops, files))


if __name__ == "__main__":
    unittest.main()
