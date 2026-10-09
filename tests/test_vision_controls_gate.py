"""Integration of the controls gate into app.vision_controls (--gate, pre-demo false MATCH line)."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from app import vision_controls

LIC = "https://example.org/licence"


def rows(pos=20, neg=20):
    out = [{"kind": "same", "ref": "p%d" % i} for i in range(pos // 2)]
    out += [{"kind": "recrop", "ref": "p%d" % i, "cand": "c%d" % i} for i in range(pos - pos // 2)]
    out += [{"kind": "different", "ref": "a%d" % i, "cand": "b%d" % i, "licence": LIC} for i in range(neg)]
    return out


def run(rs, compare, args=("--repeats", "1", "--gate")):
    with tempfile.TemporaryDirectory() as d:
        m = os.path.join(d, "secretname.json")
        with open(m, "w") as f:
            json.dump(rs, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vision_controls._main([m, *args], {}, compare, lambda p: p.encode())
    return code, buf.getvalue()


class Scripted:
    """Counting fake compare: positives (ref==cand or 'c' cand) and negatives ('b' cand) get a status
    from per-side budgets of non-passing results."""

    def __init__(self, pos_bad=0, neg_bad=0, bad_pos="NEEDS_REVIEW", bad_neg="MATCH"):
        self.calls, self.pos_bad, self.neg_bad = 0, pos_bad, neg_bad
        self.bad_pos, self.bad_neg = bad_pos, bad_neg

    def __call__(self, ref, cand):
        self.calls += 1
        if cand.startswith(b"b"):
            if self.neg_bad > 0:
                self.neg_bad -= 1
                return {"status": self.bad_neg}
            return {"status": "MISMATCH"}
        if self.pos_bad > 0:
            self.pos_bad -= 1
            return {"status": self.bad_pos}
        return {"status": "MATCH"}


class GateMode(unittest.TestCase):
    def test_invalid_manifest_zero_calls(self):
        c = Scripted()
        code, out = run(rows(pos=19), c)
        self.assertEqual((code, c.calls), (2, 0))
        self.assertIn("TOO_FEW_POSITIVE", out)
        self.assertNotIn("secretname", out)
        c = Scripted()
        code, out = run(rows(neg=19), c)
        self.assertEqual((code, c.calls), (2, 0))
        bad = rows()
        bad[-1]["licence"] = "x"
        c = Scripted()
        self.assertEqual(run(bad, c)[0], 2)
        self.assertEqual(c.calls, 0)

    def test_boundary_passes(self):
        c = Scripted(pos_bad=2, neg_bad=2, bad_neg="NEEDS_REVIEW")
        code, out = run(rows(), c)
        self.assertEqual(code, 0)
        self.assertEqual(c.calls, 40)
        self.assertTrue(out.rstrip().endswith("CONTROLS PASS"))

    def test_one_false_match_beyond_boundary_fails(self):
        code, out = run(rows(), Scripted(neg_bad=3))
        self.assertEqual(code, 1)
        self.assertIn("CONTROLS FAIL: negative, false_match", out)

    def test_false_match_alone_fails(self):
        # 19 MISMATCH + 1 MATCH on 20 negatives is 95%, still a failure
        code, out = run(rows(), Scripted(neg_bad=1))
        self.assertEqual(code, 1)
        self.assertTrue(out.rstrip().endswith("CONTROLS FAIL: false_match"))

    def test_positive_side_named_and_unavailable_counts(self):
        code, out = run(rows(), Scripted(pos_bad=3, bad_pos="UNAVAILABLE"))
        self.assertEqual(code, 1)
        self.assertIn("CONTROLS FAIL: positive", out)

    def test_repeats_count_as_runs(self):
        c = Scripted()
        code, _ = run(rows(), c, ("--repeats", "2", "--gate"))
        self.assertEqual((code, c.calls), (0, 80))

    def test_recrop_same_file_refused_before_calls(self):
        rs = rows()
        rs[-21]["cand"] = rs[-21]["ref"]
        c = Scripted()
        code, out = run(rs, c)
        self.assertEqual((code, c.calls), (2, 0))
        self.assertIn("RECROP_SAME_FILE", out)

    def test_no_paths_in_output(self):
        _, out = run(rows(), Scripted(pos_bad=5))
        for needle in ("p0", "a0", "b0", "example.org", "secretname"):
            self.assertNotIn(needle, out)


class DefaultMode(unittest.TestCase):
    def test_ten_positives_old_output_plus_false_match_line(self):
        rs = rows(pos=10, neg=10)
        for r in rs:
            r.pop("licence", None)
        c = Scripted()
        code, out = run(rs, c, ("--repeats", "1"))
        self.assertEqual(code, 0)
        self.assertIn("positive control ok", out)
        self.assertIn("false MATCH on negatives: 0", out)

    def test_false_match_exits_1(self):
        c = Scripted(neg_bad=1)
        code, out = run(rows(pos=10, neg=10), c, ("--repeats", "1"))
        self.assertEqual(code, 1)
        self.assertIn("false MATCH on negatives: 1", out)
        self.assertIn("positive control ok", out)

    def test_recrop_same_file_refused(self):
        code, out = run([{"kind": "recrop", "ref": "a", "cand": "a"}], Scripted(), ("--repeats", "1"))
        self.assertEqual(code, 2)
        self.assertIn("RECROP_SAME_FILE", out)


if __name__ == "__main__":
    unittest.main()
