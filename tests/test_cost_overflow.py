"""P6 regression by theone. Temp ledgers and fake counter; no provider calls.
Intended for the mirror/private suite; not executed by the submitting author.
"""
import json
from pathlib import Path
import tempfile
import unittest

from app.spend_cap import SpendCap, _money
from app.vision_wiring import CheckGate, _micro


CASES = ((10**400, 0.02), (1e308, 0.02), (1.0, 1.0))
NOW = 1791507600.0


class Counter:
    def maybe_purge(self):
        pass

    def allows(self, user):
        return True

    def key(self, user):
        return "fixture"


class CostOverflow(unittest.TestCase):
    def cap(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name, "ledger.json")
        cap = SpendCap(path, 2.0, clock=lambda: NOW)
        self.addCleanup(cap.close)
        return cap, path

    def ledger(self, path):
        return json.loads(path.read_text())

    def test_converters(self):
        for cost in (10**400, 1e308, -1, None, True, "0.02", float("inf"), float("nan")):
            with self.subTest(kind=type(cost).__name__):
                self.assertIsNone(_money(cost))
                self.assertEqual(_micro(cost), 20000)
        for cost in (0, -0.0, 0.0000005, 0.0000015, 0.0123456, 1.0):
            with self.subTest(cost=cost):
                expected = round(float(cost) * 1000000)
                self.assertEqual(_money(cost), expected)
                self.assertEqual(_micro(cost), expected)

    def test_settle_fallback_and_repeat(self):
        for cost, expected in CASES:
            with self.subTest(expected=expected, kind=type(cost).__name__):
                cap, path = self.cap()
                reservation = cap.reserve()
                self.assertIsNotNone(reservation)
                self.assertTrue(reservation.begin())
                reservation.close()  # request still in flight: becomes an orphan
                self.assertEqual(cap.settle(reservation.hold_id, cost), "settled")
                self.assertAlmostEqual(self.ledger(path)["spent_usd"], expected)
                self.assertEqual(self.ledger(path)["holds"], {})
                self.assertEqual(cap.settle(reservation.hold_id, cost), "repeat")
                self.assertAlmostEqual(self.ledger(path)["spent_usd"], expected)

    def test_record_fallback_clears_in_flight(self):
        for cost, expected in CASES:
            with self.subTest(expected=expected, kind=type(cost).__name__):
                cap, path = self.cap()
                reservation = cap.reserve()
                self.assertIsNotNone(reservation)
                self.assertTrue(reservation.begin())
                reservation.record(cost)
                ledger = self.ledger(path)
                self.assertAlmostEqual(ledger["spent_usd"], expected)
                self.assertEqual(ledger["holds"][reservation.hold_id]["in_flight"], 0)
                reservation.close()
                self.assertEqual(self.ledger(path)["holds"], {})

    def test_record_call_uses_same_conversion(self):
        for cost, expected in CASES:
            with self.subTest(expected=expected, kind=type(cost).__name__):
                cap, path = self.cap()
                gate = CheckGate(Counter(), cap, cap_usd=2.0, ledger_path=path, clock=lambda: NOW)
                ticket, refusal = gate.admit("fixture")
                self.assertIsNone(refusal)
                self.assertIsNotNone(ticket)
                self.assertTrue(gate.begin_call())
                self.assertEqual(gate.record_call(cost), expected <= 0.06)
                self.assertEqual(ticket.spent_micro, round(expected * 1000000))
                self.assertAlmostEqual(self.ledger(path)["spent_usd"], expected)
                hid = ticket.reservation.hold_id
                self.assertEqual(self.ledger(path)["holds"][hid]["in_flight"], 0)
                gate.settle(ticket, "UNAVAILABLE")
                self.assertEqual(self.ledger(path)["holds"], {})


if __name__ == "__main__":
    unittest.main()
