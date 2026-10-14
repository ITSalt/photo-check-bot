"""S2 + P11 integration regressions; temp ledgers only, no provider calls.
Not run against the mirror by the submitting author.
P11 (board #87387): settle() answers invalid for any id that is not 32 lowercase hex.
"""
from pathlib import Path
import hashlib
import re
import tempfile
import unittest
from app.spend_cap import SpendCap

_HOLD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def HID(label: str) -> str:
    if _HOLD_ID_RE.fullmatch(label or ""):
        return label
    return hashlib.md5(label.encode()).hexdigest()


class InvalidSettleId(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name, "ledger.json")
        self.now, self.ticks = 1791507600.0, 0

    def clock(self):
        self.ticks += 1
        return self.now

    def cap(self):
        c = SpendCap(self.path, 2.0, clock=self.clock)
        self.addCleanup(c.close)
        return c

    def snapshot(self, c):
        return (c._day, c._spent, c._loaded, c._now, c._on_disk, self.ticks,
                {k: (h.id, h.day, h.left, h.in_flight, h.orphan, h.at)
                 for k, h in c._holds.items()},
                {k: (m.kind, m.usd, m.at) for k, m in c._memo.items()},
                self.path.read_bytes())

    def test_invalid_is_no_event_with_due_maintenance(self):
        c = self.cap()
        r = c.reserve()
        self.assertIsNotNone(r)
        self.assertTrue(r.begin())
        r.close()  # persisted orphan; a valid event after the jump would expire it
        self.assertEqual(c.settle(HID("old"), 0.01), "unknown")
        before = self.snapshot(c)
        self.now += 15 * 86400  # day rollover, orphan expiry and memo expiry all due
        bad = (b"ab", ("ab",), 5, 1.5, True, None, [], {}, "", frozenset(), float("nan"),
               "5", " ", "ab", "h1", "A" * 32, "0123456789abcdef0123456789abcde")  # 31 hex + uppercase
        for hid in bad:
            with self.subTest(hid=repr(hid)[:40]):
                self.assertEqual(c.settle(hid, 0.10), "invalid")
                self.assertEqual(self.snapshot(c), before)

    def test_invalid_does_not_load_or_rewrite_existing_ledger(self):
        c = self.cap()
        self.assertEqual(c.settle(HID("seed"), 0.01), "unknown")
        c.close()
        c = self.cap()  # an existing file is loaded lazily
        self.assertFalse(c._loaded)
        before = self.snapshot(c)
        self.assertEqual(c.settle([], 0.10), "invalid")
        self.assertEqual(self.snapshot(c), before)

    def test_short_and_uppercase_strings_are_invalid(self):  # P11: was "nonempty strings remain valid"
        c = self.cap()
        before = self.snapshot(c)
        for hid in ("5", " ", "ab", "h1", "0123456789ABCDEF0123456789abcdef"):
            with self.subTest(hid=hid):
                self.assertEqual(c.settle(hid, 0.01), "invalid")
                self.assertEqual(self.snapshot(c), before)
        self.assertEqual(c._spent, 0)
        self.assertEqual(c._memo, {})

    def test_32_hex_strings_remain_valid(self):
        c = self.cap()
        for label in ("5", "seed", "ab", "h1"):
            hid = HID(label)
            with self.subTest(hid=hid):
                self.assertEqual(c.settle(hid, 0.01), "unknown")
                self.assertEqual(c.settle(hid, 0.01), "repeat")
        self.assertEqual(c._spent, 40000)
        self.assertEqual(set(c._memo), {HID(x) for x in ("5", "seed", "ab", "h1")})

    def test_lookalikes_cannot_raise_high_water_even_after_restart(self):
        c = self.cap()
        for valid_label, invalid in (("b'h'", b"h"), ("('h',)", ("h",)), ("5", 5)):
            valid = HID(valid_label)
            with self.subTest(valid=valid):
                self.assertEqual(c.settle(valid, 0.01), "unknown")
                before = self.snapshot(c)
                self.assertEqual(c.settle(invalid, 0.10), "invalid")
                self.assertEqual(self.snapshot(c), before)
                c.close()
                c = self.cap()
                self.assertEqual(c.settle(valid, 0.01), "repeat")
                self.assertEqual(c._memo[valid].usd, 10000)
        self.assertEqual(c._spent, 30000)


if __name__ == "__main__":
    unittest.main()
