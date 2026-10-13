"""WP-CONTROLS-06: a hold is settled once. The real SpendCap against the pure model of board task S1.

tests/settle_once_model.py is theone's accepted entry, byte for byte (board task S1, post f7631721). The
bot does not import it: it is the oracle here.

(a) Recorded traces: the four traces of the task's public fixture, recorded from app/spend_cap.py at
    18d6caa, replayed on the real SpendCap (fake clock, temp dir) must give the REQUIRED columns.
(b) Differential: seeded random traces run through both the real SpendCap and the model; every result,
    every booked amount, the counted total and the whole remembered state must agree after every step.

Mapping of the model's operations (micro-dollars there, dollars here):
  reserve -> SpendCap.reserve(), with the model's amount = the hold size (3 x 0.02 USD);
  record  -> Reservation.begin() + Reservation.record(cost);
  close   -> Reservation.close();
  crash   -> the SpendCap is closed and a new one opened on the same file;
  settle  -> SpendCap.settle(hold id, cost).
Model rules with no counterpart in the real module, excluded by construction:
  1. reserve of an id that is held or remembered (refused by the model): a real id is a fresh uuid;
  2. reserve of another amount than the hold size;
  3. record or close of a hold taken before a crash: the dead process's Reservation is fenced (its
     SpendCap is closed), so nothing of it reaches the ledger; such ids are only settled;
  4. close of an id that is not a live hold (the model's "noop"): a second Reservation.close() does not
     touch the ledger at all.
The model breaks a tie between two equally old remembered ids by the smaller id, so in the random traces
the real hold ids are made the model's ids (uuid4 is patched to hand out the id the model was given).
One difference in time, removed by the test: the model looks at the clock at a crash (it expires and
forgets as of that second, before the holds become orphans), the real module only when an operation
runs. With a short memory that moves the moment an expired id is forgotten (theone's finding, board msg
58bd3f89), so here the dying owner looks at the ledger in its last second, through a begin() on a hold
that does not exist. For the same reason the ledger's total at a crash step is compared with the total
of the model's state: the model's counted() already leaves out the orphans that expire at the next look.

More traces: SETTLE_ONCE_TRACES=3000 python3 -m unittest tests.test_settle_once"""
import collections, copy, datetime, hashlib, json, logging, os, random, sys, tempfile, types, unittest, uuid
from unittest import mock

from app.spend_cap import Reservation, SpendCap
from tests import settle_once_model as model

HERE = os.path.dirname(os.path.abspath(__file__))
M = 1_000_000
HOLD = 60000                                    # 3 calls x 0.02 USD, in micro-dollars
MODEL_SHA256 = "f0a4052953d287a1db5bc7a67cde7d1086ddf74e04427cf0eb8a26ad1d88c654"
TRACES = int(os.environ.get("SETTLE_ONCE_TRACES", "12"))
STEPS = 60
# board/kit/settle-once/fixtures.public.json of the task, byte for byte. A row is
# [t, op, id, usd, result at 18d6caa, counted at 18d6caa, REQUIRED result, REQUIRED booked, REQUIRED counted].
FIXTURE_SHA256 = "606a0149cfbcddbf9ba40be6af20cc2d3610d6ed75d861a1db547e0206743fd0"
FIXTURE = r"""{"t0":1728003600,"cfg":{"cap":200000,"lifetime":600,"memory":1209600,"memory_max":2000},"traces":{"A_orphan_settled_three_times":[[0,"reserve","h1",60000,"held",60000,"held",0,60000],[40,"crash","",0,"crashed",60000,"crashed",0,60000],[300,"settle","h1",10000,"settled",10000,"settled",10000,10000],[301,"settle","h1",10000,"unknown",20000,"repeat",0,10000],[302,"settle","h1",10000,"unknown",30000,"repeat",0,10000]],"B_settle_after_release_and_live":[[0,"reserve","h1",60000,"held",60000,"held",0,60000],[3,"record","h1",4000,"recorded",60000,"recorded",4000,60000],[4,"settle","h1",20000,"live",60000,"live",0,60000],[6,"close","h1",0,"released",4000,"released",0,4000],[60,"settle","h1",20000,"unknown",24000,"released",0,4000]],"C_cap_refusal_and_unknown_id":[[0,"reserve","h1",60000,"held",60000,"held",0,60000],[1,"reserve","h2",60000,"held",120000,"held",0,120000],[2,"reserve","h3",60000,"held",180000,"held",0,180000],[3,"reserve","h4",60000,"refused",180000,"refused",0,180000],[10,"settle","zz",10000,"unknown",190000,"unknown",10000,190000],[11,"settle","zz",10000,"unknown",200000,"repeat",0,190000]],"D_orphan_expires_then_late_settle":[[82000,"reserve","h1",60000,"held",60000,"held",0,60000],[82010,"crash","",0,"crashed",60000,"crashed",0,60000],[83000,"settle","zz",0,"unknown",0,"unknown",0,0],[84000,"settle","h1",15000,"unknown",15000,"expired",15000,15000],[84001,"settle","h1",15000,"unknown",30000,"repeat",0,15000]]}}"""


def micro(usd):
    return round(usd * M)


def day_of(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat()


class Real:
    """The real SpendCap on a ledger in a temp dir, with a fake clock, driven by the model's events."""

    def __init__(self, test, cap, lifetime, now, **kw):
        self.tmp = tempfile.TemporaryDirectory(); test.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "spend_cap.json")
        self.now = now
        self.new = lambda: SpendCap(self.path, cap / M, clock=lambda: self.now, orphan_lifetime_s=lifetime, **kw)
        self.cap = self.new(); test.addCleanup(lambda: self.cap.close())
        self.res, self.ids = {}, {}               # name -> this process's Reservation; name -> hold id
        self.shapes = set()

    def hold_id(self, name):
        return self.ids.setdefault(name, name)    # never reserved: the name itself is the made-up id

    def ledger(self):
        with open(self.path) as f:
            led = json.load(f)
        self.shapes.add((tuple(led), tuple(sorted({k for m in led["memo"].values() for k in m}))))
        names = {v: k for k, v in self.ids.items()}
        return {"day": led["day"], "spent": micro(led["spent_usd"]),
                "holds": {names[k]: micro(h["usd"]) for k, h in led["holds"].items()},
                "memory": {names[k]: {"kind": m["k"], "usd": micro(m["usd"]), "at": m["at"]}
                           for k, m in led["memo"].items()}}

    def apply(self, event, look_at_crash=True):
        """One model event on the real module -> (result, booked, counted), all read from the ledger."""
        before = self.ledger()
        self.now = event["t"]
        op, name = event["op"], event.get("id")
        if op == "reserve":
            r = self.cap.reserve()
            if r is not None:
                self.res[name], self.ids[name] = r, r.hold_id
            result = "refused" if r is None else "held"
        elif op == "record":
            self.res[name].begin()
            self.res[name].record(event["usd"] / M)
            result = "recorded"
        elif op == "close":
            self.res[name].close()
            result = None                           # read from the ledger below
        elif op == "crash":
            if look_at_crash:
                Reservation(self.cap, "no such hold").begin()
            self.cap.close()
            self.cap, self.res = self.new(), {}
            self.cap.orphans()
            result = "crashed"
        else:
            result = self.cap.settle(self.hold_id(name), event["usd"] / M)
        after = self.ledger()
        if op == "close":
            result = "released" if name in before["holds"] and name not in after["holds"] else "noop"
        booked = after["spent"] - (before["spent"] if after["day"] == before["day"] else 0)
        return result, booked, after["spent"] + sum(after["holds"].values())


class Quiet(unittest.TestCase):
    def setUp(self):
        logging.getLogger("spend_cap").disabled = True
        self.addCleanup(setattr, logging.getLogger("spend_cap"), "disabled", False)


class AcceptedEntry(unittest.TestCase):
    def test_model_is_the_accepted_board_entry_byte_for_byte(self):
        with open(os.path.join(HERE, "settle_once_model.py"), "rb") as f:
            raw = f.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), MODEL_SHA256)
        self.assertTrue(raw.startswith(b"# S1 ledger by TheOne."))
        self.assertEqual(hashlib.sha256(FIXTURE.encode("utf-8")).hexdigest(), FIXTURE_SHA256)


class RecordedTraces(Quiet):
    """(a) What app/spend_cap.py answered at 18d6caa is in the fixture next to what is required now."""

    def events(self, fixture, rows):
        for t, op, hid, usd, _was, _was_counted, want, booked, counted in rows:
            e = {"op": op, "t": fixture["t0"] + t}
            if hid: e["id"] = hid
            if op in ("reserve", "record", "settle"): e["usd"] = usd
            yield e, (want, booked, counted)

    def test_real_spend_cap_gives_the_required_columns(self):
        fixture = json.loads(FIXTURE)
        cfg = fixture["cfg"]
        self.assertEqual(sorted(fixture["traces"]), ["A_orphan_settled_three_times", "B_settle_after_release_and_live",
                                                     "C_cap_refusal_and_unknown_id", "D_orphan_expires_then_late_settle"])
        rows = 0
        for name, trace in fixture["traces"].items():
            real = Real(self, cfg["cap"], cfg["lifetime"], fixture["t0"], settle_memory_s=cfg["memory"],
                        settle_memory_max=cfg["memory_max"])
            for e, want in self.events(fixture, trace):
                self.assertEqual(e.get("usd", HOLD) if e["op"] == "reserve" else HOLD, HOLD)
                self.assertEqual(real.apply(e, look_at_crash=False), want, (name, e))
                rows += 1
        self.assertEqual(rows, 21)

    def test_model_gives_the_required_columns(self):
        fixture = json.loads(FIXTURE)
        for name, trace in fixture["traces"].items():
            s = model.new_state()
            for e, want in self.events(fixture, trace):
                s, r = model.apply(s, e, fixture["cfg"])
                s = json.loads(json.dumps(s))
                self.assertEqual((r["result"], r["booked"], model.counted(s, e["t"], fixture["cfg"])), want, (name, e))

    def test_the_fixture_shows_what_changed(self):
        """At 18d6caa a second settle booked again and a settle after a release booked; not any more."""
        fixture = json.loads(FIXTURE)
        changed = sorted({(row[4], row[6]) for trace in fixture["traces"].values() for row in trace if row[4] != row[6]})
        self.assertEqual(changed, [("unknown", "expired"), ("unknown", "released"), ("unknown", "repeat")])
        a = fixture["traces"]["A_orphan_settled_three_times"]
        self.assertEqual(([r[5] for r in a[2:]], [r[8] for r in a[2:]]), ([10000, 20000, 30000], [10000] * 3))


class Differential(Quiet):
    """(b) Random traces through the real SpendCap and the model, compared after every step."""

    def trace(self, seed, seen, steps=STEPS):
        next_id = []
        with mock.patch.object(uuid, "uuid4", lambda: types.SimpleNamespace(hex=next_id[-1])):
            self.one_trace(seed, seen, steps, next_id)

    def one_trace(self, seed, seen, steps, next_id):
        rng = random.Random(seed)
        cap = rng.choice([200000, 120000, 2000000])
        lifetime = rng.choice([600, 600, None])
        memory = rng.choice([1209600, 1209600, 1209600, 86400, 5000, 700, 0])
        memory_max = rng.choice([2000, 2000, 2000, 3, 1, 0])
        cfg = {"cap": cap, "lifetime": float("inf") if lifetime is None else lifetime,
               "memory": memory, "memory_max": memory_max}
        t = 20000 * 86400 + rng.choice([3600, 80000])
        real = Real(self, cap, lifetime, t, settle_memory_s=memory, settle_memory_max=memory_max)
        s, known, fresh = model.new_state(), [], iter(range(10 ** 6))
        for step in range(steps):
            t += rng.choice([0, 1, 1, 5, 30, 30, 120, 599, 601, 3600, 3600, 40000, 86400, 700000])
            op = rng.choice(["reserve", "reserve", "record", "record", "close", "crash", "settle", "settle", "settle"])
            e = {"op": op, "t": t}
            if op == "reserve":
                e.update(id="%032x" % next(fresh), usd=HOLD)      # always a new id (rule 1), the hold size (2)
                next_id.append(e["id"])
            elif op == "record":
                if not real.res: continue                         # only this process's holds (rule 3)
                e.update(id=rng.choice(sorted(real.res)), usd=rng.choice([0, 4000, 12300, 20000, 70000]))
            elif op == "close":
                live = sorted(n for n in real.res if n in s["holds"])
                if not live: continue                             # only a live hold, once (rules 3, 4)
                e.update(id=rng.choice(live))
            elif op == "settle":
                ids = known[-8:] if known and rng.random() < 0.8 else ["z0", "z1", "z2"]   # mostly real holds
                e.update(id=rng.choice(ids), usd=rng.choice([0, 5000, 10000, 30000, 65000]))
            given = copy.deepcopy(s)
            s2, r = model.apply(s, e, cfg)
            self.assertEqual(s, given, "apply changed the state it was given")
            s = json.loads(json.dumps(s2))                        # the model keeps nothing outside the state
            counted = model.counted(s, t, cfg)
            self.assertEqual(s, json.loads(json.dumps(s2)), "counted changed the state it was given")
            if op == "crash":                                     # counted() already leaves out the orphans that
                counted = s["spent"] + sum(h["left"] for h in s["holds"].values())   # expire at the next look;
            got = real.apply(e)                                   # the ledger drops them at the next operation
            where = (seed, step, e, cfg)
            self.assertEqual(got, (r["result"], r["booked"], counted), where)
            self.assertEqual(real.ids.get(e.get("id"), e.get("id")), e.get("id"), where)   # the same ids on both sides
            led = real.ledger()                                   # and the whole state, not only the totals
            self.assertEqual(led["spent"], s["spent"], where)
            self.assertEqual(led["day"], day_of(s["day"] * 86400), where)
            self.assertEqual(led["holds"], {k: h["left"] for k, h in s["holds"].items()}, where)
            self.assertEqual(led["memory"], s["memory"], where)
            if op == "reserve" and r["result"] == "held":
                known.append(e["id"])
            seen[(op, r["result"])] += 1
            seen[("booked", op, r["booked"] > 0)] += 1
        self.assertEqual(real.shapes - {(("day", "spent_usd", "holds", "memo"), ()),
                                        (("day", "spent_usd", "holds", "memo"), ("at", "k", "usd"))}, set())

    def test_real_spend_cap_agrees_with_the_model(self):
        seen = collections.Counter()
        for seed in range(TRACES):
            self.trace(seed, seen)
        for key in [("reserve", "held"), ("reserve", "refused"), ("record", "recorded"), ("close", "released"),
                    ("crash", "crashed"), ("settle", "live"), ("settle", "settled"), ("settle", "repeat"),
                    ("settle", "released"), ("settle", "expired"), ("settle", "unknown"),
                    ("booked", "settle", True), ("booked", "settle", False)]:
            self.assertGreater(seen[key], 0, "the traces never reached %r" % (key,))
        if os.environ.get("SETTLE_ONCE_TRACES"):
            steps = sum(v for k, v in seen.items() if k[0] != "booked")
            settles = {k[1]: v for k, v in sorted(seen.items()) if k[0] == "settle"}
            print("\nreal SpendCap vs model: %d traces, %d steps, all agree; settle results %s"
                  % (TRACES, steps, settles), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
