"""WP-CONTROLS-01: per-attribute codes in the live log and controls runs; recompute round trip.

Fake transports only. No network, no keys.
"""
import io
import json
import logging
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout

from app import recompute, vision_compare, vision_controls, vision_core
from app.photo_compare import run_bot_pair
from tests.test_t4_vision import (ENV, EXTRA, FINE, INV, JPEG, PNG, FakeModel, Session, chat,
                                  compare_answer)

MARK = "ZQMARKERZQ"


def record_of(per_order, inventory=INV):
    v = vision_core.parse_inventory(chat({"attributes": inventory}))
    a, b = [vision_core.parse_compare(chat(compare_answer(v["sha256"], o, u, attrs=inventory)), v)
            for o, u in per_order]
    return vision_core.code_record(v, a, b), vision_core.assess(v, a, b)


def both(overrides, unmapped=()):
    return [(overrides, unmapped)] * 2


class CodeStringTests(unittest.TestCase):
    def check(self, per_order, expected, status, inventory=INV):
        record, result = record_of(per_order, inventory)
        self.assertEqual(vision_core.format_codes(record), expected)
        self.assertEqual((record["a"], result["status"]), (status, status))

    def test_match(self):
        self.check(both(None), "1MDD011C,2MDD011C,3MDD001C;n", "MATCH")

    def test_mismatch(self):
        self.check(both({"print.layout": ("MISMATCH", "DIRECT")}), "1MDD011C,2XDD010D,3MDD001C;n", "MISMATCH")

    def test_each_review_reason(self):
        self.check(both({"button.count": ("MATCH", "PARTIAL")}), "1NPP011W,2MDD011C,3MDD001C;n", "NEEDS_REVIEW")
        self.check(both({"button.count": ("NOT_VISIBLE_IN_REFERENCE", "NONE")}),
                   "1VNN010V,2MDD011C,3MDD001C;n", "NEEDS_REVIEW")
        self.check(both({"button.count": ("MISMATCH", "NONE")}), "1NNN010V,2MDD011C,3MDD001C;n", "NEEDS_REVIEW")
        self.check([(None, ()), ({"button.count": ("MISMATCH", "DIRECT")}, ())],
                   "1ADD010O,2MDD011C,3MDD001C;n", "NEEDS_REVIEW")
        self.check(both({"logo.text": ("MATCH", "PARTIAL")}), "1NPP111F,2MDD011C;n", "NEEDS_REVIEW",
                   inventory=[FINE, INV[1]])

    def test_extra_element(self):
        self.check(both(None, (EXTRA,)), "1MDD011C,2MDD011C,3MDD001C;c", "MISMATCH")
        self.check([(None, (EXTRA,)), (None, ())], "1MDD011C,2MDD011C,3MDD001C;u", "NEEDS_REVIEW")
        occluded = {**EXTRA, "visibility_in_reference": "OCCLUDED_IN_REFERENCE"}
        self.check(both(None, (occluded,)), "1MDD011C,2MDD011C,3MDD001C;u", "NEEDS_REVIEW")

    def test_line_shape_alphabet_and_cap(self):
        record, _ = record_of(both(None))
        self.assertEqual(vision_core.codes_line(record), "check_codes v=3 a=MATCH n=3 codes=1MDD011C,2MDD011C,3MDD001C;n")
        worst = {"a": "MATCH", "extra": "unconfirmed",
                 "attrs": [{"pos": i, "outcome": "ASYMMETRIC_PASS", "evidence": ("PARTIAL", "NONE"),
                            "fine": True, "tlc": True, "both_match": True,
                            "cls": "fine_detail_unverified"} for i in range(1, 13)]}
        codes = vision_core.format_codes(worst)
        self.assertTrue(codes.endswith(";u"))      # 12 attributes fit the cap untruncated
        self.assertRegex(codes, r"^[0-9A-Za-z,;]+$")
        worst["attrs"] = worst["attrs"] * 5
        self.assertLessEqual(len(vision_core.format_codes(worst)), vision_core.CODES_MAX)

    def test_marker_in_every_model_text_never_in_record(self):
        inv = [{"attribute_id": MARK + ".id%d" % i, "claim": MARK + " claim: x%d" % i,
                "reference_roi": a["reference_roi"]} for i, a in enumerate(INV)]
        extra = {**EXTRA, "description": MARK + " pocket"}
        v = vision_core.parse_inventory(chat({"attributes": inv}))
        answers = []
        for _ in range(2):
            a = compare_answer(v["sha256"], None, (extra,), attrs=inv)
            for r in a["results"]:
                r["observation"] = MARK + " seen"
            answers.append(vision_core.parse_compare(chat(a), v))
        record = vision_core.code_record(v, *answers)
        self.assertNotIn(MARK, repr(record))
        self.assertNotIn(MARK, vision_core.codes_line(record))
        self.assertNotIn("id0", vision_core.codes_line(record))


class LiveLogTests(unittest.TestCase):
    def run_chain(self, model):
        compare = vision_compare.compare_from_env(ENV, transport=model)
        return run_bot_pair(Session(), compare, None)["status"]

    def codes_lines(self, logs):
        return [line for line in logs.output if "check_codes " in line]

    def test_one_line_per_completed_check(self):
        diff = ({"print.layout": ("MISMATCH", "DIRECT")}, ())
        with self.assertLogs("vision", "INFO") as logs:
            self.run_chain(FakeModel())
            self.run_chain(FakeModel(per_order=(diff, diff)))
        lines = self.codes_lines(logs)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("check_codes v=3 a=MATCH n=3 codes=1MDD011C,2MDD011C,3MDD001C;n"))
        self.assertTrue(lines[1].endswith("check_codes v=3 a=MISMATCH n=3 codes=1MDD011C,2XDD010D,3MDD001C;n"))
        self.assertLess(max(len(x) for x in lines), 300)

    def test_none_for_unavailable_and_refusals(self):
        def drop(i, a):
            a["results"].pop()
        with self.assertLogs("vision", "INFO") as logs:
            logging.getLogger("vision").info("sentinel")
            self.assertEqual(self.run_chain(FakeModel(mutate=drop)), "UNAVAILABLE")
            self.assertEqual(self.run_chain(FakeModel(fail_at=1, reply={"status": 500, "body": None})),
                             "UNAVAILABLE")
            vision_compare.compare_from_env(ENV, transport=FakeModel())(b"%PDF " + PNG, PNG)
        self.assertEqual(self.codes_lines(logs), [])

    def test_marker_never_in_log(self):
        inv = [{**a, "attribute_id": MARK + ".x%d" % i, "claim": MARK + ": c"} for i, a in enumerate(INV)]
        extra = {**EXTRA, "description": MARK}

        def mark(i, a):
            for r in a["results"]:
                r["observation"] = MARK
        model = FakeModel(inventory=inv, per_order=((None, (extra,)),) * 2, mutate=mark)
        with self.assertLogs("vision", "DEBUG") as logs:
            self.run_chain(model)
        self.assertEqual(len(self.codes_lines(logs)), 1)
        self.assertNotIn(MARK, repr(logs.output))

    def test_codes_failure_never_changes_the_check(self):
        class Core:
            def __getattr__(self, name):
                if name == "code_record":
                    raise RuntimeError("boom")
                return getattr(vision_core, name)
        compare = vision_compare.compare_from_env(ENV, transport=FakeModel(), core=Core())
        self.assertEqual(compare(JPEG, PNG)["status"], "MATCH")

    def test_on_codes_gets_record_and_assess_result_unchanged(self):
        got = []
        compare = vision_compare.compare_from_env(ENV, transport=FakeModel(), on_codes=got.append)
        result = compare(JPEG, PNG)
        self.assertEqual(set(result), {"status", "total", "confirmed", "to_check", "differs", "extra",
                                      "not_compared"})
        self.assertEqual([r["a"] for r in got], ["MATCH"])


def run_main(rows, make, args=("--repeats", "1")):
    with tempfile.TemporaryDirectory() as d:
        m = os.path.join(d, "manifest.json")
        c = os.path.join(d, "secretcodes.txt")
        with open(m, "w") as f:
            json.dump(rows, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vision_controls._main([m, *args, "--codes", c], ENV, None, lambda p: p.encode(), make)
        with open(c) as f:
            text = f.read()
        return code, buf.getvalue(), text


def model_make(models):
    """make(environ, on_reject, on_codes) -> compare running the real chain on a fake model."""
    it = iter(models)

    def make(environ, on_reject=None, on_codes=None):
        def compare(ref, cand):
            return vision_compare.compare_from_env(ENV, transport=next(it), on_codes=on_codes)(JPEG, PNG)
        return compare
    return make


class ControlsCodesTests(unittest.TestCase):
    ROWS = [{"kind": "same", "ref": "privatefile1"},
            {"kind": "recrop", "ref": "privatefile2", "cand": "privatefile3"},
            {"kind": "different", "ref": "privatefile4", "cand": "privatefile5"}]

    def models(self):
        diff = ({"print.layout": ("MISMATCH", "DIRECT")}, ())
        weak = ({"button.count": ("MATCH", "PARTIAL")}, ())
        return [FakeModel(), FakeModel(per_order=(weak, weak)), FakeModel(per_order=(diff, diff))]

    def test_round_trip(self):
        code, out, text = run_main(self.ROWS, model_make(self.models()))
        self.assertEqual(len(text.splitlines()), 3)
        self.assertEqual(text.splitlines()[0], "kind=same check_codes v=3 a=MATCH n=3 codes=1MDD011C,2MDD011C,3MDD001C;n")
        self.assertNotIn("privatefile", text + out)
        self.assertNotIn("secretcodes", out)
        result = recompute.recompute(text.splitlines())
        self.assertEqual((result["lines"], result["bad"], result["a_diff"]), (3, 0, 0))
        self.assertEqual(result["rules"]["A"]["same"]["MATCH"], 1)
        self.assertEqual(result["rules"]["A"]["recrop"]["NEEDS_REVIEW"], 1)
        self.assertEqual(result["rules"]["A"]["different"]["MISMATCH"], 1)

    def test_recompute_main_over_the_codes_file(self):
        code, out, text = run_main(self.ROWS, model_make(self.models()))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "codes.txt")
            with open(p, "w") as f:
                f.write(text)
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(recompute._main([p]), 0)
        self.assertIn("lines=3", buf.getvalue())

    def test_unavailable_run_line(self):
        bad = FakeModel(fail_at=1, reply={"status": 500, "body": None})
        code, out, text = run_main(self.ROWS[:1], model_make([bad]))
        self.assertEqual(text, "kind=same unavailable\n")
        result = recompute.recompute(text.splitlines())
        self.assertEqual(result["rules"]["A"]["same"]["UNAVAILABLE"], 1)

    def test_stale_record_is_not_reused_after_unavailable(self):
        bad = FakeModel(fail_at=1, reply={"status": 500, "body": None})
        code, out, text = run_main(self.ROWS[:1] * 2, model_make([FakeModel(), bad]))
        self.assertEqual([x.split(" ")[1] for x in text.splitlines()], ["check_codes", "unavailable"])

    def test_codes_file_unusable_exit_2_no_path(self):
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, "m.json")
            with open(m, "w") as f:
                json.dump(self.ROWS[:1], f)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = vision_controls._main([m, "--codes", os.path.join(d, "nodir", "secretcodes")], {},
                                             lambda a, b: {"status": "MATCH"}, lambda p: b"x")
        self.assertEqual(code, 2)
        self.assertNotIn("nodir", buf.getvalue())

    def test_summary_percentages_have_one_decimal(self):
        self.assertEqual([vision_controls.pct1(x) for x in (64 / 66, 1.0, 0.0, 2 / 3, 0.8995, 0.9)],
                         ["97.0%", "100.0%", "0.0%", "66.7%", "90.0%", "90.0%"])
        statuses = iter(["MATCH"] * 64 + ["NEEDS_REVIEW"] * 2)
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, "m.json")
            with open(m, "w") as f:
                json.dump([{"kind": "same", "ref": "a"}] * 66, f)
            buf = io.StringIO()
            with redirect_stdout(buf):
                vision_controls._main([m, "--repeats", "1"], {}, lambda a, b: {"status": next(statuses)},
                                      lambda p: b"x")
        self.assertIn("MATCH=97.0%", buf.getvalue())
        self.assertIn("NEEDS_REVIEW=3.0%", buf.getvalue())

    def test_without_codes_flag_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, "m.json")
            with open(m, "w") as f:
                json.dump(self.ROWS[:1], f)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = vision_controls._main([m, "--repeats", "1"], {}, lambda a, b: {"status": "MATCH"},
                                             lambda p: b"x")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
