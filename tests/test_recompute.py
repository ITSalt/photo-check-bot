"""WP-CONTROLS-01: offline A/B/C recompute."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from app import recompute, vision_core
from tests.test_t4_vision import EXTRA, FINE, INV, chat, compare_answer

MARK = "ZQMARKERZQ"


def line(a, codes, kind=None, prefix=""):
    n = len(codes.split(";")[0].split(","))
    return "%s%scheck_codes v=1 a=%s n=%d codes=%s" % (prefix, "kind=%s " % kind if kind else "", a, n, codes)


def attrs(n, bad=None, bad_cls="D"):
    """codes body for n attributes, all confirmed except attribute `bad` (1-based)."""
    items = []
    for i in range(1, n + 1):
        cls = bad_cls if i == bad else "C"
        outcome = {"C": "M", "D": "X"}.get(cls, "N")
        items.append("%d%sDD0%s" % (i, outcome, cls))
    return ",".join(items)


def twelve(bad=None, bad_cls="D", extra="n"):
    return attrs(12, bad, bad_cls) + ";" + extra


class RuleTests(unittest.TestCase):
    def test_rule_a(self):
        self.assertEqual(recompute.rule_a(["C", "C"], "none"), "MATCH")
        self.assertEqual(recompute.rule_a(["C", "D"], "none"), "MISMATCH")
        self.assertEqual(recompute.rule_a(["C", "W"], "none"), "NEEDS_REVIEW")
        self.assertEqual(recompute.rule_a(["C", "C"], "confirmed"), "MISMATCH")
        self.assertEqual(recompute.rule_a(["C", "C"], "unconfirmed"), "NEEDS_REVIEW")
        self.assertEqual(recompute.rule_a(["D", "W"], "none"), "MISMATCH")
        self.assertEqual(recompute.rule_a(["W", "W"], "confirmed"), "MISMATCH")

    def test_rule_c_ignores_attributes_9_to_12(self):
        for pos in (9, 10, 11, 12):
            for cls, a in (("D", "MISMATCH"), ("W", "NEEDS_REVIEW")):
                r = recompute.recompute([line(a, twelve(pos, cls), "same")])
                self.assertEqual(r["a_diff"], 0)
                self.assertEqual(r["rules"]["C"]["same"]["MATCH"], 1, (pos, cls))
                self.assertEqual(r["rules"]["A"]["same"]["MATCH"], 0)
        for pos in (1, 8):
            r = recompute.recompute([line("MISMATCH", twelve(pos, "D"), "same")])
            self.assertEqual(r["rules"]["C"]["same"]["MISMATCH"], 1)
        r = recompute.recompute([line("NEEDS_REVIEW", twelve(8, "W"), "same")])
        self.assertEqual(r["rules"]["C"]["same"]["NEEDS_REVIEW"], 1)
        # the extra-element outcome is kept by C
        r = recompute.recompute([line("MISMATCH", twelve(extra="c"), "same")])
        self.assertEqual(r["rules"]["C"]["same"]["MISMATCH"], 1)

    def test_a_matches_assess_on_fixtures(self):
        """Records from real validated answers: recomputed A == assess status on every line."""
        weak, diff = ("MATCH", "PARTIAL"), ("MISMATCH", "DIRECT")
        cases = [([(None, ()), (None, ())], INV), ([({"button.count": weak}, ())] * 2, INV),
                 ([({"print.layout": diff}, ())] * 2, INV),
                 ([(None, ()), ({"button.count": diff}, ())], INV),
                 ([(None, (EXTRA,)), (None, (EXTRA,))], INV),
                 ([(None, (EXTRA,)), (None, ())], INV),
                 ([({"logo.text": weak}, ())] * 2, [FINE, INV[1]]),
                 ([({"button.count": ("NOT_VISIBLE_IN_REFERENCE", "NONE")}, ())] * 2, INV)]
        lines, statuses = [], []
        for per_order, inventory in cases:
            v = vision_core.parse_inventory(chat({"attributes": inventory}))
            a, b = [vision_core.parse_compare(chat(compare_answer(v["sha256"], o, u, attrs=inventory)), v)
                    for o, u in per_order]
            lines.append(vision_core.codes_line(vision_core.code_record(v, a, b)))
            statuses.append(vision_core.assess(v, a, b)["status"])
        self.assertEqual(sorted(set(statuses)), ["MATCH", "MISMATCH", "NEEDS_REVIEW"])
        for text, status in zip(lines, statuses):
            self.assertIn("a=%s " % status, text)
            parsed = list(recompute.read_lines([text]))[0]
            self.assertEqual(recompute.rule_a(parsed[2], parsed[3]), status)
        r = recompute.recompute(lines)
        self.assertEqual((r["lines"], r["a_diff"], r["bad"]), (len(cases), 0, 0))

    def test_recorded_a_mismatch_counted_exit_1(self):
        lines = [line("MATCH", attrs(2, 1) + ";n"), line("MATCH", attrs(2) + ";n")]
        self.assertEqual(recompute.recompute(lines)["a_diff"], 1)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.txt")
            with open(p, "w") as f:
                f.write("\n".join(lines))
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(recompute._main([p]), 1)
        self.assertIn("recorded A differs from recomputed A: 1", buf.getvalue())


class ReportTests(unittest.TestCase):
    def test_shares_and_b(self):
        lines = [line("MATCH", attrs(2) + ";n", "same"), line("NEEDS_REVIEW", attrs(2, 1, "W") + ";n", "recrop"),
                 "kind=same unavailable", line("MATCH", attrs(2) + ";n", "different"),
                 line("MISMATCH", attrs(2, 2) + ";n", "different"),
                 "2026-10-10 INFO vision " + line("NEEDS_REVIEW", attrs(3, 2, "W") + ";n"),
                 "unrelated log line"]
        out = "\n".join(recompute.report(recompute.recompute(lines)))
        self.assertIn("lines=6", out)
        self.assertIn("rule B: n/a (no check_codes v=2 or v=3 lines)", out)
        self.assertIn("rule A: positive MATCH 33.3% (runs=3) false MATCH on negatives 1 NEEDS_REVIEW 40.0% (checks=5)", out)

    def test_bot_log_line_without_kind(self):
        r = recompute.recompute(["INFO vision " + line("MATCH", attrs(2) + ";n")])
        self.assertEqual(r["rules"]["A"][None]["MATCH"], 1)

    def test_malformed_is_counted_not_printed(self):
        bad = ["check_codes v=1 a=MATCH n=2 codes=1MDD0C;n", "check_codes v=2 a=MATCH n=1 codes=1MDD0C;n",
               line("MATCH", "1MDD0C,3MDD0C;n"), "check_codes v=1 a=MATCH n=1 codes=1MDD0C;x",
               "check_codes v=1 a=MATCH n=1 codes=1MDD0C;n " + MARK + "x"]
        r = recompute.recompute(bad + [line("MATCH", attrs(1) + ";n")])
        self.assertEqual(r["bad"], 4)   # the trailing-text line still parses; v=2 does not
        out = "\n".join(recompute.report(r))
        self.assertNotIn(MARK, out)
        self.assertIn("unparsable check_codes lines: 4", out)

    def test_output_never_prints_line_content(self):
        text = line("MATCH", attrs(2) + ";n", "same", prefix=MARK + " ")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "secretlog.txt")
            with open(p, "w") as f:
                f.write(text + "\n" + MARK + " unrelated\ncheck_codes " + MARK + "\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                recompute._main([p])
        self.assertNotIn(MARK, buf.getvalue())
        self.assertNotIn("secretlog", buf.getvalue())

    def test_usage_and_unreadable(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(recompute._main([]), 2)
            self.assertEqual(recompute._main(["/nonexistent/secretname"]), 2)
        self.assertNotIn("secretname", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
