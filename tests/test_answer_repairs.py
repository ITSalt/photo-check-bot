"""WP-BOT-15 / D-20: ROI clamp before T2 validation, JSON tail tolerance, prompt line."""
import contextlib
import copy
import functools
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import vision_compare, vision_controls, vision_core, vision_request
from app.vision_request import AnswerError
from tests.test_answer_diagnostics import MARK, CaptureLogs
from tests.test_t4_vision import ENV, INV, JPEG, PNG, FakeModel, chat, compare_answer, reasons, run

LINE = "Every box must lie inside 0..1000 (x+w<=1000, y+h<=1000), and nothing may follow the JSON object."


def with_roi(roi, index=0):
    out = copy.deepcopy(INV)
    out[index]["reference_roi"] = roi
    return out


def status_of(model):
    compare = vision_compare.compare_from_env(ENV, transport=model)
    return compare(JPEG, PNG)


class Clamp(unittest.TestCase):
    def test_clamp_roi(self):
        self.assertEqual(vision_core.clamp_roi([903, 100, 100, 400]), [903, 100, 97, 400])
        self.assertEqual(vision_core.clamp_roi([100, 950, 200, 200]), [100, 950, 200, 50])
        self.assertEqual(vision_core.clamp_roi([900, 900, 300, 300]), [900, 900, 100, 100])
        self.assertIsNone(vision_core.clamp_roi([900, 100, 100, 400]))      # exactly on the edge
        self.assertIsNone(vision_core.clamp_roi([1000, 0, 5, 5]))           # x outside [0, 1000)
        self.assertIsNone(vision_core.clamp_roi([-1, 0, 5000, 5]))
        self.assertIsNone(vision_core.clamp_roi([0, 0, 0, 2000]))           # w < 1: T2 decides
        self.assertIsNone(vision_core.clamp_roi([1, 2, 3]))
        self.assertIsNone(vision_core.clamp_roi(["a", 0, 1, 1]))
        self.assertIsNone(vision_core.clamp_roi([True, 0, 2000, 1]))
        self.assertIsNone(vision_core.clamp_roi(None))

    def test_inventory_clamped_and_hashed_clamped(self):
        fixes = []
        v = vision_core.parse_inventory(chat({"attributes": with_roi([903, 100, 100, 400])}), fixes)
        self.assertEqual(v["inventory"]["attributes"][0]["reference_roi"], [903, 100, 97, 400])
        self.assertEqual(fixes, [("ROI_CLAMPED", 1)])
        inside = vision_core.parse_inventory(chat({"attributes": with_roi([903, 100, 97, 400])}))
        self.assertEqual(v["sha256"], inside["sha256"])

    def test_inventory_still_rejected(self):
        for roi, rule in (([1000, 100, 50, 50], "ROI_OUT_OF_FRAME"),     # x = 1000
                          ([995, 0, 100, 5], "ROI_OUT_OF_FRAME"),        # 5 x 5 after clamping
                          ([0, 0, 0, 50], "ROI_OUT_OF_FRAME")):
            with self.subTest(roi=roi), self.assertRaises(AnswerError) as ctx:
                vision_core.parse_inventory(chat({"attributes": with_roi(roi)}))
            self.assertEqual((ctx.exception.args, ctx.exception.rule), (("BAD_INVENTORY",), rule))

    def test_compare_clamped_everywhere(self):
        v = vision_core.parse_inventory(chat({"attributes": INV}))
        answer = compare_answer(v["sha256"])
        answer["results"][0]["reference_roi"] = [903, 100, 100, 400]
        answer["results"][1]["candidate_roi"] = [100, 903, 300, 400]
        answer["unmapped_candidate_elements"] = [{"candidate_roi": [950, 950, 100, 100], "description": "d",
                                                  "visibility_in_reference": "VISIBLE_AND_ABSENT"}]
        fixes = []
        out = vision_core.parse_compare(chat(answer), v, fixes)
        self.assertEqual(out["results"][0]["reference_roi"], [903, 100, 97, 400])
        self.assertEqual(out["results"][1]["candidate_roi"], [100, 903, 300, 97])
        self.assertEqual(out["unmapped_candidate_elements"][0]["candidate_roi"], [950, 950, 50, 50])
        # a bare T2 answer has no candidate_view: read as IN_VIEW and counted (WP-CONTROLS-04)
        self.assertEqual(fixes, [("VIEW_DEFAULTED", len(INV)), ("ROI_CLAMPED", 3)])

    def test_compare_still_rejected(self):
        v = vision_core.parse_inventory(chat({"attributes": INV}))
        for roi in ([1000, 0, 5, 5], [998, 0, 100, 40]):                     # x = 1000; 2 x 40 = 80 after clamping
            answer = compare_answer(v["sha256"])
            answer["results"][0]["candidate_roi"] = roi
            with self.subTest(roi=roi), self.assertRaises(AnswerError) as ctx:
                vision_core.parse_compare(chat(answer), v)
            self.assertEqual((ctx.exception.args, ctx.exception.rule), (("BAD_COMPARE",), "ROI_OUT_OF_FRAME"))

    def test_status_unchanged_through_the_chain(self):
        clean = status_of(FakeModel())
        edge = copy.deepcopy(INV)
        edge[0]["reference_roi"] = [103, 100, 900, 400]       # x+w = 1003
        model = FakeModel(inventory=edge)
        with self.assertLogs("vision", "INFO") as logs:
            result = status_of(model)
        self.assertEqual(result["status"], clean["status"])
        self.assertEqual(result["status"], "MATCH")
        text = "\n".join(logs.output)
        self.assertIn("vision_answer_fixed stage=inventory rule=ROI_CLAMPED n=1", text)
        self.assertIn("vision_answer_fixed stage=compare_a rule=ROI_CLAMPED n=1", text)
        self.assertIn("vision_answer_fixed stage=compare_b rule=ROI_CLAMPED n=1", text)
        self.assertNotIn("1003", text)

    def test_clamped_box_and_fine_detail_marker(self):
        # A clamped reference box is smaller, so it may cross FINE_ROI_AREA (20000):
        # [900, 900, 200, 200] = 40000 is coarse; clamped to 100 x 100 = 10000 it is fine.
        # The marker only changes the REASON of an attribute already under review; the status
        # of the check does not move (T2 decides the outcomes, assess maps them to the status).
        edge, inside = [900, 900, 200, 200], [900, 900, 100, 100]
        self.assertFalse(vision_core.is_fine(edge))
        self.assertTrue(vision_core.is_fine(vision_core.clamp_roi(edge)))
        for per_order in ([(None, ())] * 2,
                          [({"button.count": ("MATCH", "PARTIAL")}, ())] * 2,
                          [(None, ()), ({"button.count": ("MISMATCH", "DIRECT")}, ())],
                          [({"button.count": ("MISMATCH", "NONE")}, ())] * 2):
            with self.subTest(per_order=per_order):
                with_edge = run(per_order, with_roi(edge))
                in_frame = run(per_order, with_roi(inside))
                coarse = run(per_order, with_roi([100, 100, 200, 200]))
                self.assertEqual(with_edge["status"], in_frame["status"])
                self.assertEqual(with_edge["status"], coarse["status"])
                self.assertEqual(with_edge, in_frame)    # the clamped box IS the in-frame box
        weak = [({"button.count": ("MATCH", "PARTIAL")}, ())] * 2
        self.assertEqual(reasons(run(weak, with_roi(edge))), reasons(run(weak, with_roi(inside))))


class Tail(unittest.TestCase):
    def parse(self, text, fixes=None):
        return vision_request.extract_answer(chat(text), fixes)

    def test_tolerated_tails(self):
        for tail in ("}", "```", "\n```\n", "  \n", "}]", "} ```", "]"):
            fixes = []
            with self.subTest(tail=tail):
                self.assertEqual(self.parse('{"a": 1}' + tail, fixes), {"a": 1})
                self.assertEqual(fixes, [("TRAILING_TAIL", 1)] if tail.strip() else [])

    def test_other_tails_are_not_json(self):
        for tail in ("abc", "} abc", "{}", ' {"b": 2}', "```json", "}x", "0"):
            with self.subTest(tail=tail), self.assertRaises(AnswerError) as ctx:
                self.parse('{"a": 1}' + tail)
            self.assertEqual(ctx.exception.args, ("NOT_JSON",))

    def test_unchanged_behaviour(self):
        for text, code in (('{"a": 1', "NOT_JSON"), ("nope", "NOT_JSON"), ("[1]", "NOT_OBJECT"), ("", "EMPTY_ANSWER")):
            with self.subTest(text=text), self.assertRaises(AnswerError) as ctx:
                self.parse(text)
            self.assertEqual(ctx.exception.args, (code,))
        self.assertEqual(self.parse('  \n{"a": 1}'), {"a": 1})

    def test_chain_with_a_tail_logs_the_code_only(self):
        class Tailed(FakeModel):
            def __call__(self, request, headers, timeout):
                reply = super().__call__(request, headers, timeout)
                choice = reply["body"]["choices"][0]["message"]
                choice["content"] += "\n```"
                return reply
        with self.assertLogs("vision", "INFO") as logs:
            self.assertEqual(status_of(Tailed())["status"], "MATCH")
        fixed = [l for l in logs.output if "vision_answer_fixed" in l]
        self.assertEqual(len(fixed), 3)
        self.assertIn("stage=inventory rule=TRAILING_TAIL", fixed[0])
        self.assertTrue(all(l.endswith("rule=TRAILING_TAIL") for l in fixed))


class Prompt(unittest.TestCase):
    def test_both_instructions_carry_the_box_and_tail_line(self):
        self.assertIn(LINE, vision_request.INVENTORY_INSTRUCTION)
        self.assertIn(LINE, vision_core.COMPARE_INSTRUCTION)


class Privacy(unittest.TestCase):
    def repaired_model(self):
        edge = copy.deepcopy(INV)
        edge[0].update(reference_roi=[103, 100, 900, 400], claim=MARK, attribute_id=MARK + ".x")
        edge[1].update(claim=MARK + MARK)

        class M(FakeModel):
            def __call__(inner, request, headers, timeout):
                reply = super(M, inner).__call__(request, headers, timeout)
                if reply["status"] == 200:
                    msg = reply["body"]["choices"][0]["message"]
                    msg["content"] = msg["content"].replace("Same in both.", MARK) + "}"
                return reply
        return M(inventory=edge)

    def test_repairs_never_log_model_values(self):
        with CaptureLogs() as logs:
            self.assertEqual(status_of(self.repaired_model())["status"], "MATCH")
        self.assertIn("vision_answer_fixed", logs.text)
        self.assertNotIn(MARK, logs.text)
        self.assertNotIn("ZQ", logs.text)

    def test_junk_tail_with_marker_logs_the_code_only(self):
        class M(FakeModel):
            def __call__(self, request, headers, timeout):
                reply = super().__call__(request, headers, timeout)
                reply["body"]["choices"][0]["message"]["content"] += "abc" + MARK
                return reply
        with CaptureLogs() as logs:
            self.assertEqual(status_of(M()), {"status": "UNAVAILABLE"})
        self.assertIn("code=NOT_JSON", logs.text)
        self.assertNotIn(MARK, logs.text)

    def test_controls_table_counts_repairs_per_stage(self):
        rows = [{"kind": "same", "ref": "a"}]
        with tempfile.TemporaryDirectory() as d:
            manifest = os.path.join(d, "m.json")
            with open(manifest, "w") as f:
                json.dump(rows, f)
            model = self.repaired_model()

            class Reset:
                def __call__(self, request, headers, timeout):
                    if sum(p["type"] == "image_url" for p in request["messages"][0]["content"]) == 1:
                        model.requests = []
                    return model(request, headers, timeout)
            fake = functools.partial(vision_compare.compare_from_env, transport=Reset())
            out = io.StringIO()
            with patch.object(vision_compare, "compare_from_env", fake), \
                    contextlib.redirect_stdout(out), CaptureLogs() as logs:
                vision_controls._main([manifest, "--repeats", "2"], ENV, read=lambda p: JPEG)
        text = out.getvalue()
        self.assertIn("repaired answers by stage x rule:", text)
        for stage in ("inventory", "compare_a", "compare_b"):
            self.assertIn("  stage=%s rule=ROI_CLAMPED n=2" % stage, text)
            self.assertIn("  stage=%s rule=TRAILING_TAIL n=2" % stage, text)
        self.assertNotIn(MARK, text + logs.text)


if __name__ == "__main__":
    unittest.main()
