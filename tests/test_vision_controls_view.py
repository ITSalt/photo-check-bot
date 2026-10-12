"""WP-CONTROLS-04, scope 7: the controls run. Summary and --gate are unchanged in logic; the
repaired-answers table also counts VIEW_DEFAULTED; the codes file carries v=3 lines."""
import contextlib
import functools
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import recompute, vision_compare, vision_controls, vision_core
from app.answer_rules import FIXES, RejectTally
from tests.test_out_of_view import HIDDEN, both, inventory
from tests.test_t4_vision import ENV, JPEG, FakeModel

MARK = "ZQMARKERZQ"


def run_main(rows, models, args=("--repeats", "1")):
    """vision_controls._main over the real chain, one fake model per run."""
    it = iter(models)

    class Transport:
        def __init__(self):
            self.model = None

        def __call__(self, request, headers, timeout):
            if sum(p["type"] == "image_url" for p in request["messages"][0]["content"]) == 1:
                self.model = next(it)            # an inventory call starts a new run
            return self.model(request, headers, timeout)
    fake = functools.partial(vision_compare.compare_from_env, transport=Transport())
    with tempfile.TemporaryDirectory() as d:
        manifest, codes = os.path.join(d, "m.json"), os.path.join(d, "codes.txt")
        with open(manifest, "w") as f:
            json.dump(rows, f)
        out = io.StringIO()
        with patch.object(vision_compare, "compare_from_env", fake), contextlib.redirect_stdout(out):
            code = vision_controls._main([manifest, *args, "--codes", codes], ENV, read=lambda p: JPEG)
        with open(codes) as f:
            return code, out.getvalue(), f.read()


class TallyTests(unittest.TestCase):
    def test_view_defaulted_is_counted_under_its_own_name(self):
        tally = vision_controls.ControlsTally()
        tally.add("compare_a", "FIXED", "VIEW_DEFAULTED")
        tally.add("compare_a", "FIXED", "VIEW_DEFAULTED")
        tally.add("compare_b", "FIXED", "VIEW_DEFAULTED")
        tally.add("compare_b", "FIXED", "ROI_CLAMPED")
        tally.add("inventory", "FIXED", MARK)                  # an unknown fix stays OTHER
        tally.add(MARK + " stage", "FIXED", "VIEW_DEFAULTED")   # a stage outside the alphabet
        tally.add("compare_a", "BAD_COMPARE", "VIEW_DEFAULTED")   # not a reject rule: OTHER
        for _ in range(3):
            tally.add("compare_b", "FIXED", "VIEW_VERDICT_FIXED")
        tally.add("compare_a", "FIXED", "VIEW_ROI_FILLED")
        text = "\n".join(tally.lines())
        self.assertIn("  stage=compare_a rule=VIEW_DEFAULTED n=2", text)
        self.assertIn("  stage=compare_b rule=VIEW_DEFAULTED n=1", text)
        self.assertIn("  stage=compare_b rule=ROI_CLAMPED n=1", text)
        self.assertIn("  stage=inventory rule=OTHER n=1", text)
        self.assertIn("  stage=unknown rule=VIEW_DEFAULTED n=1", text)
        self.assertIn("  stage=compare_a rule=OTHER n=1", text)
        self.assertIn("  stage=compare_b rule=VIEW_VERDICT_FIXED n=3", text)
        self.assertIn("  stage=compare_a rule=VIEW_ROI_FILLED n=1", text)
        self.assertNotIn(MARK, text)

    def test_other_counts_are_the_base_tally(self):
        ours, base = vision_controls.ControlsTally(), RejectTally()
        for args in (("inventory", "BAD_INVENTORY", "ROI_SHAPE"), ("compare_a", "FIXED", "TRAILING_TAIL"),
                     ("compare_b", "BAD_COMPARE", "nope"), ("x" * 30, "FIXED", "ROI_CLAMPED")):
            ours.add(*args)
            base.add(*args)
        self.assertEqual(ours.lines(), base.lines())
        self.assertEqual(vision_compare.ANSWER_FIXES,
                         FIXES + ("VIEW_DEFAULTED", "VIEW_VERDICT_FIXED", "VIEW_ROI_FILLED"))
        self.assertEqual((vision_core.VIEW_DEFAULTED, vision_core.VIEW_VERDICT_FIXED, vision_core.VIEW_ROI_FILLED),
                         vision_compare.VIEW_FIXES)
        self.assertEqual(vision_controls.VIEW_DEFAULTED, "VIEW_DEFAULTED")


class ControlsRunTests(unittest.TestCase):
    ROWS = [{"kind": "same", "ref": "a"}, {"kind": "recrop", "ref": "a", "cand": "b"},
            {"kind": "different", "ref": "a", "cand": "c"}]

    def models(self):
        inv = inventory(4, logo_at=3)
        return [FakeModel(inventory=inv, per_order=both({"a3": HIDDEN})),           # MATCH, one detail not compared
                FakeModel(inventory=inv, per_order=both({"a3": ("MISMATCH", "NONE")}), views=False),   # no field
                FakeModel(inventory=inv, per_order=both({"a3": HIDDEN, "a0": ("MISMATCH", "DIRECT")}))]

    def test_codes_file_table_and_summary(self):
        code, out, text = run_main(self.ROWS, self.models())
        self.assertEqual(text.splitlines(), [
            "kind=same check_codes v=3 a=MATCH n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110H;n",
            "kind=recrop check_codes v=3 a=NEEDS_REVIEW n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110V;n",
            "kind=different check_codes v=3 a=MISMATCH n=4 codes=1XDD000D,2MDD001C,3MDD001C,4NNN110H;n"])
        # the second model left candidate_view out: one repaired answer per compare call
        self.assertIn("repaired answers by stage x rule:", out)
        self.assertIn("  stage=compare_a rule=VIEW_DEFAULTED n=1", out)
        self.assertIn("  stage=compare_b rule=VIEW_DEFAULTED n=1", out)
        self.assertIn("rejects by stage x rule: none", out)
        self.assertIn("false MATCH on negatives: 0", out)
        self.assertIn("RULE TOO STRICT", out)                  # 1 MATCH of 2 positives: the summary rule is the old one
        self.assertEqual(code, 1)
        r = recompute.recompute(text.splitlines())
        self.assertEqual((r["lines"], r["bad"], r["a_diff"], r["inconsistent"]), (3, 0, 0, 0))
        self.assertEqual(r["rules"]["A'"]["same"]["MATCH"], 1)
        self.assertEqual(r["rules"]["A"]["same"]["NEEDS_REVIEW"], 1)

    def test_the_table_counts_the_out_of_view_repairs(self):
        inv = inventory(4, logo_at=3)

        def broken(index, answer):
            answer["results"][3].update(candidate_view="OUT_OF_VIEW", verdict="MATCH", evidence_quality="NONE",
                                        candidate_roi=None)
        code, out, text = run_main([{"kind": "same", "ref": "a"}], [FakeModel(inventory=inv, mutate=broken)])
        self.assertEqual(text.splitlines(),
                         ["kind=same check_codes v=3 a=MATCH n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110H;n"])
        for stage in ("compare_a", "compare_b"):
            self.assertIn("  stage=%s rule=VIEW_VERDICT_FIXED n=1" % stage, out)
            self.assertIn("  stage=%s rule=VIEW_ROI_FILLED n=1" % stage, out)
        self.assertIn("rejects by stage x rule: none", out)

    def test_a_false_match_on_a_negative_still_fails_the_run(self):
        # a different product whose only differing detail is "out of view": MATCH on a negative row
        inv = inventory(4, logo_at=3)
        rows = [{"kind": "different", "ref": "a", "cand": "c"}]
        code, out, text = run_main(rows, [FakeModel(inventory=inv, per_order=both({"a3": HIDDEN}))])
        self.assertIn("false MATCH on negatives: 1", out)
        self.assertEqual(code, 1)

    def test_gate_logic_is_unchanged(self):
        inv = inventory(4, logo_at=3)
        diff = both({"a3": HIDDEN, "a0": ("MISMATCH", "DIRECT")})
        rows = ([{"kind": "same", "ref": "p%d" % i} for i in range(20)]
                + [{"kind": "different", "ref": "r%d" % i, "cand": "c%d" % i, "licence": "https://creativecommons.org/publicdomain/zero/1.0/"} for i in range(20)])
        models = ([FakeModel(inventory=inv, per_order=both({"a3": HIDDEN})) for _ in range(20)]
                  + [FakeModel(inventory=inv, per_order=diff) for _ in range(20)])
        code, out, text = run_main(rows, models, args=("--repeats", "1", "--gate"))
        self.assertIn("CONTROLS PASS", out)
        self.assertEqual(code, 0)
        self.assertEqual(len(text.splitlines()), 40)


if __name__ == "__main__":
    unittest.main()
