"""WP-CONTROLS-04: code record v=3 (class letter H) in the offline recompute and the codes log."""
import io
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout

from app import codes_log, recompute, vision_core
from tests.test_out_of_view import HIDDEN, inventory, line_of, both, parsed

MARK = "ZQMARKERZQ"
OWNER_V2 = ("2026-10-11 check_codes v=2 a=NEEDS_REVIEW n=8 codes=1MDD001C,2MDD101C,3MDD101C,4NNN110V,5NPP001W,"
            "6MDD001C,7MDD001C,8APN100V;n")
MATCH_H = "check_codes v=3 a=MATCH n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110H;n"
REVIEW_H = ("check_codes v=3 a=NEEDS_REVIEW n=8 codes=1MDD001C,2MDD101C,3MDD101C,4NNN110H,5NPP001W,6MDD001C,"
            "7MDD001C,8APN100V;n")
FEW_H = "check_codes v=3 a=NEEDS_REVIEW n=4 codes=1MDD001C,2MDD001C,3NNN000H,4NNN000H;n"


def status(result, rule, kind=None):
    return {s: n for s, n in result["rules"][rule][kind].items() if n}


class ParseTests(unittest.TestCase):
    def test_v3_reads_h_and_the_v2_fields(self):
        attrs, extra = recompute.parse_attrs("3", 4, "1MDD001C,2MDD001C,3MDD001C,4NNN110H;n")
        self.assertEqual(extra, "none")
        self.assertEqual(attrs[3], {"outcome": "N", "ev": ("N", "N"), "fine": True, "tlc": True,
                                    "both_match": False, "cls": "H"})
        self.assertEqual(recompute.parse_codes(4, "1MDD001C,2MDD001C,3MDD001C,4NNN110H;n", "3"),
                         (["C", "C", "C", "H"], "none"))

    def test_h_is_a_v3_letter_only(self):
        for version in ("1", "2"):
            with self.assertRaises(recompute.BadLine):
                recompute.parse_attrs(version, 1, "1NNN000H;n" if version == "2" else "1NNN0H;n")
        r = recompute.recompute([MATCH_H.replace("v=3", "v=2")])
        self.assertEqual((r["bad"], r["lines"]), (1, 1))

    def test_unknown_versions_are_refused(self):
        for version in ("0", "4", "", "33", None, 3):
            with self.assertRaises(recompute.BadLine):
                recompute.parse_attrs(version, 1, "1MDD001C;n")
        r = recompute.recompute(["check_codes v=4 a=MATCH n=1 codes=1MDD001C;n"])
        self.assertEqual(r["bad"], 1)

    def test_class_h_must_agree_with_the_evidence(self):
        good = ["check_codes v=3 a=NEEDS_REVIEW n=1 codes=1NNN000H;n", "check_codes v=3 a=NEEDS_REVIEW n=1 codes=1VNN000H;n",
                "check_codes v=3 a=NEEDS_REVIEW n=1 codes=1ANN000H;n", "check_codes v=3 a=NEEDS_REVIEW n=1 codes=1NNN000V;n"]
        self.assertEqual(recompute.recompute(good)["inconsistent"], 0)
        bad = ["check_codes v=3 a=NEEDS_REVIEW n=1 codes=1NPN000H;n",     # PARTIAL in one order
               "check_codes v=3 a=NEEDS_REVIEW n=1 codes=1NNP000H;n",
               "check_codes v=3 a=NEEDS_REVIEW n=1 codes=1NPP000H;n",
               "check_codes v=3 a=NEEDS_REVIEW n=1 codes=1MDD001H;n",     # a MATCH is never out of view
               "check_codes v=3 a=MISMATCH n=1 codes=1XDD000H;n"]         # nor a seen difference
        r = recompute.recompute(bad)
        self.assertEqual(r["inconsistent"], len(bad))


class RuleTests(unittest.TestCase):
    def test_rule_a_prime_on_letters(self):
        f = recompute.rule_a_prime
        self.assertEqual(f(list("CCCH"), "none"), "MATCH")
        self.assertEqual(f(list("CCHH"), "none"), "NEEDS_REVIEW")           # under 3 confirmed
        self.assertEqual(f(list("CCCHHHH"), "none"), "NEEDS_REVIEW")        # 3 of 7: under half
        self.assertEqual(f(list("CCCCHHH"), "none"), "MATCH")               # 4 of 7
        self.assertEqual(f(list("CCCHHH"), "none"), "MATCH")                # 3 of 6
        self.assertEqual(f(list("CC"), "none"), "NEEDS_REVIEW")
        self.assertEqual(f(list("CCC"), "none"), "MATCH")
        self.assertEqual(f(list("CCCH"), "unconfirmed"), "NEEDS_REVIEW")
        self.assertEqual(f(list("CCCH"), "confirmed"), "MISMATCH")
        self.assertEqual(f(list("CCDH"), "none"), "MISMATCH")
        self.assertEqual(f(list("HHHH"), "confirmed"), "MISMATCH")
        for letter in "VOWF":
            self.assertEqual(f(list("CCCC") + [letter], "none"), "NEEDS_REVIEW", letter)

    def test_old_rule_a_counts_h_as_not_visible(self):
        self.assertEqual(recompute.rule_a(list("CCCH"), "none"), "NEEDS_REVIEW")
        self.assertEqual(recompute.rule_a(list("CCCV"), "none"), "NEEDS_REVIEW")
        self.assertEqual(recompute.rule_a(list("CCC"), "none"), "MATCH")
        self.assertEqual(recompute.rule_a(list("CC"), "none"), "MATCH")      # no minimum before D-10

    def test_the_letters_rule_is_the_production_rule(self):
        """recompute.rule_a_prime (letters) == vision_core.rule_a_prime (class names), exhaustively
        over every class multiset of up to 5 attributes and on random longer ones."""
        letter = vision_core.CODE_CLASS
        names = list(letter)
        rng = random.Random(7)
        cases = [[]]
        for _ in range(5):
            cases = [c + [n] for c in cases for n in names] + [[]]
        cases += [[rng.choice(names) for _ in range(rng.randint(6, 12))] for _ in range(3000)]
        for classes in cases:
            if not classes:
                continue
            for extra in ("none", "unconfirmed", "confirmed"):
                self.assertEqual(recompute.rule_a_prime([letter[c] for c in classes], extra),
                                 vision_core.rule_a_prime(classes, extra)[0], (classes, extra))
        self.assertEqual(recompute.MIN_CONFIRMED, vision_core.MIN_CONFIRMED)

    def test_a_prime_equals_the_recorded_status_on_real_records(self):
        """Criterion 5: every line the chain writes recomputes to its own `a=` under A'."""
        weak, diff = ("MATCH", "PARTIAL"), ("MISMATCH", "DIRECT")
        extra = {"candidate_roi": [300, 300, 200, 200], "description": "extra pocket",
                 "visibility_in_reference": "VISIBLE_AND_ABSENT"}
        cases = [(inventory(4), both(None)), (inventory(4), both({"a3": HIDDEN})),
                 (inventory(4), both({"a2": HIDDEN, "a3": HIDDEN})), (inventory(8), both({"a%d" % i: HIDDEN for i in range(3, 8)})),
                 (inventory(2), both(None)), (inventory(4), both({"a3": HIDDEN, "a0": weak})),
                 (inventory(4), both({"a3": HIDDEN, "a0": diff})), (inventory(4), both({"a3": HIDDEN}, (extra,))),
                 (inventory(4), [({"a3": HIDDEN}, (extra,)), ({"a3": HIDDEN}, ())]),
                 (inventory(4), [({"a3": HIDDEN}, ()), ({"a3": ("MISMATCH", "NONE")}, ())])]
        lines, recorded = [], []
        for inv, per_order in cases:
            lines.append(line_of(inv, per_order))
            v, answers = parsed(inv, per_order)
            recorded.append(vision_core.assess(v, *answers)["status"])
            self.assertIn(" a=%s " % recorded[-1], lines[-1])
            self.assertEqual(vision_core.code_record(v, *answers)["a"], recorded[-1])     # status_of is A' as well
        self.assertEqual(sorted(set(recorded)), ["MATCH", "MISMATCH", "NEEDS_REVIEW"])
        r = recompute.recompute(lines)
        self.assertEqual((r["lines"], r["bad"], r["a_diff"], r["inconsistent"]), (len(cases), 0, 0, 0))
        self.assertEqual(status(r, "A'"), {s: recorded.count(s) for s in set(recorded)})
        for line in lines:
            self.assertTrue(recompute._LINE.search(line))

    def test_old_a_stands_next_to_a_prime(self):
        r = recompute.recompute(["kind=same " + MATCH_H, "kind=recrop " + REVIEW_H, "kind=same " + FEW_H])
        self.assertEqual((r["a_diff"], r["bad"], r["inconsistent"]), (0, 0, 0))
        self.assertEqual((r["rules"]["A'"]["same"]["MATCH"], r["rules"]["A'"]["same"]["NEEDS_REVIEW"]), (1, 1))
        self.assertEqual(r["rules"]["A"]["same"]["NEEDS_REVIEW"], 2)       # H as not_visible: no MATCH
        self.assertEqual(r["rules"]["A"]["same"]["MATCH"], 0)
        self.assertEqual(r["rules"]["A'"]["recrop"]["NEEDS_REVIEW"], 1)
        out = "\n".join(recompute.report(r))
        self.assertIn("rule A': positive MATCH 33.3% (runs=3)", out)
        self.assertIn("rule A: positive MATCH 0.0% (runs=3)", out)

    def test_a_wrong_recorded_status_is_counted(self):
        for wrong in (MATCH_H.replace("a=MATCH", "a=NEEDS_REVIEW"),       # the old rule's answer on a v=3 line
                      FEW_H.replace("a=NEEDS_REVIEW", "a=MATCH"),         # MATCH without the minimum
                      REVIEW_H.replace("a=NEEDS_REVIEW", "a=MATCH")):
            self.assertEqual(recompute.recompute([wrong])["a_diff"], 1, wrong)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "codes.txt")
            with open(p, "w") as f:
                f.write(FEW_H.replace("a=NEEDS_REVIEW", "a=MATCH") + "\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(recompute._main([p]), 1)
        self.assertIn("recorded A differs from recomputed A: 1", buf.getvalue())

    def test_b_and_c_are_built_on_a_prime_for_v3_lines(self):
        # C: an H among the first 8 is not "to check" (under the old rule A it would block MATCH)
        mixed = ",".join(["%dMDD001C" % i for i in range(1, 8)] + ["8NNN000H", "9NPP001W"])
        r = recompute.recompute(["check_codes v=3 a=NEEDS_REVIEW n=9 codes=%s;n" % mixed])
        self.assertEqual((status(r, "A'"), status(r, "A"), status(r, "C")),
                         ({"NEEDS_REVIEW": 1}, {"NEEDS_REVIEW": 1}, {"MATCH": 1}))
        # B: the coarse PARTIAL attribute #5 counts as confirmed, #8 (one order saw nothing) does not
        r = recompute.recompute([REVIEW_H])
        self.assertEqual(status(r, "B"), {"NEEDS_REVIEW": 1})
        line = "check_codes v=3 a=NEEDS_REVIEW n=5 codes=1MDD001C,2MDD001C,3NPP001W,4NNN110H,5NNN000H;n"
        r = recompute.recompute([line])
        self.assertEqual((status(r, "A'"), status(r, "A"), status(r, "B")),
                         ({"NEEDS_REVIEW": 1}, {"NEEDS_REVIEW": 1}, {"MATCH": 1}))    # 3 of 5 once #3 counts
        line = "check_codes v=3 a=NEEDS_REVIEW n=6 codes=1MDD001C,2MDD001C,3NPP001W,4NNN110H,5NNN000H,6NNN000H;n"
        self.assertEqual(status(recompute.recompute([line]), "B"), {"MATCH": 1})          # 3 of 6: half
        line = "check_codes v=3 a=NEEDS_REVIEW n=4 codes=1MDD001C,2NPP001W,3NNN110H,4NNN000H;n"
        self.assertEqual(status(recompute.recompute([line]), "B"), {"NEEDS_REVIEW": 1})   # B keeps the minimum
        # C: the first 8 attributes only, under A' (the minimum counts within those 8)
        nine = ",".join(["%dMDD001C" % i for i in range(1, 9)] + ["9NPP001W"])
        r = recompute.recompute(["check_codes v=3 a=NEEDS_REVIEW n=9 codes=%s;n" % nine])
        self.assertEqual((status(r, "A'"), status(r, "C")), ({"NEEDS_REVIEW": 1}, {"MATCH": 1}))
        hidden8 = ",".join(["%dMDD001C" % i for i in range(1, 4)] + ["%dNNN000H" % i for i in range(4, 9)]
                           + ["%dMDD001C" % i for i in range(9, 12)])
        r = recompute.recompute(["check_codes v=3 a=MATCH n=11 codes=%s;n" % hidden8])
        self.assertEqual((status(r, "A'"), status(r, "C")), ({"MATCH": 1}, {"NEEDS_REVIEW": 1}))   # 3 of the first 8

    def test_old_lines_keep_their_numbers(self):
        """v=1 / v=2 lines: A must equal `a=`, B and C stay on rule A, and A' does not count them."""
        v2 = "kind=same check_codes v=2 a=MATCH n=2 codes=1MDD001C,2MDD001C;n"
        v1 = "kind=same check_codes v=1 a=MATCH n=2 codes=1MDD0C,2MDD0C;n"
        r = recompute.recompute([v2, v1, OWNER_V2])
        self.assertEqual((r["lines"], r["bad"], r["a_diff"], r["inconsistent"], r["v1"]), (3, 0, 0, 0, 1))
        self.assertEqual(r["rules"]["A"]["same"]["MATCH"], 2)          # no minimum on an old line
        self.assertEqual(r["rules"]["C"]["same"]["MATCH"], 2)
        self.assertEqual(r["rules"]["B"]["same"]["MATCH"], 1)
        self.assertEqual(status(r, "A"), {"NEEDS_REVIEW": 1})          # the owner's line
        self.assertEqual(sum(sum(t.values()) for t in r["rules"]["A'"].values()), 0)
        out = "\n".join(recompute.report(r))
        self.assertIn("rule A': n/a (no check_codes v=3 lines)", out)
        self.assertIn("rule A: positive MATCH 100.0% (runs=2)", out)

    def test_mixed_file_and_unavailable_runs(self):
        lines = ["kind=same " + MATCH_H, "kind=same check_codes v=2 a=MATCH n=2 codes=1MDD001C,2MDD001C;n",
                 "kind=same unavailable", "kind=different " + MATCH_H]
        r = recompute.recompute(lines)
        self.assertEqual((r["lines"], r["a_diff"]), (4, 0))
        out = "\n".join(recompute.report(r))
        self.assertIn("rule A': positive MATCH 50.0% (runs=2) false MATCH on negatives 1", out)
        self.assertIn("rule A: positive MATCH 33.3% (runs=3) false MATCH on negatives 0", out)

    def test_output_has_no_line_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "codes.txt")
            with open(p, "w") as f:
                f.write(MARK + " " + MATCH_H + "\n" + REVIEW_H + " " + MARK + "\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(recompute._main([p]), 0)
        self.assertNotIn(MARK, buf.getvalue())
        self.assertNotIn("NNN110H", buf.getvalue())


class CodesLogTests(unittest.TestCase):
    """Scope 6: the durable codes log follows the recompute parser, so a v=3 line is kept."""

    def test_a_v3_line_is_accepted_by_the_codes_log(self):
        for line in (MATCH_H, REVIEW_H, FEW_H, line_of(inventory(4), both({"a3": HIDDEN}))):
            self.assertTrue(codes_log.valid(line), line)

    def test_v2_lines_are_still_accepted_and_forgeries_refused(self):
        self.assertTrue(codes_log.valid(OWNER_V2[11:]))
        for forged in (MATCH_H.replace("v=3", "v=2"),                  # H under v=2
                       MATCH_H.replace("v=3", "v=4"), MATCH_H + " x", MATCH_H.replace("4NNN110H", "4NNN110Z"),
                       MATCH_H.replace("n=4", "n=5")):
            self.assertFalse(codes_log.valid(forged), forged)


if __name__ == "__main__":
    unittest.main()
