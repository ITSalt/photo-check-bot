"""WP-CONTROLS-05 (D-12 a): the zoom check for fine-detail attributes, an experiment in the
controls tool. Fake transports and a fake imaging library only: no network, no keys, no Pillow
(one test of the real crop runs only where Pillow happens to be installed).
"""
import contextlib
import importlib.util
import io
import json
import logging
import math
import os
import pathlib
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import recompute, vision_compare, vision_controls, vision_core, vision_request, vision_wiring
from app import vision_controls_zoom as vz
from app.intake.sniff import sniff_image
from app.vision_request import AnswerError, ImageRejected
from tests.test_t4_vision import ENV, JPEG, KEY, PNG, FakeModel, chat, compare_answer, parts_of, png

MARK = "ZQMARKERZQ"
ROOT = pathlib.Path(__file__).resolve().parent.parent
BIG = [100, 100, 300, 400]             # coarse
FINE_A = [500, 600, 100, 100]          # fine, area 10000
FINE_B = [200, 200, 50, 60]            # fine, area 3000: the smallest, asked first
CAND = [120, 90, 300, 400]             # the candidate box of every fake compare result
S, A, T = "PRESENT_SAME", "ABSENT_OR_DIFFERENT", "CANNOT_TELL"
ORDER = {"MATCH": 0, "NEEDS_REVIEW": 1, "MISMATCH": 2}


def attr(i, roi, name="деталь"):
    return {"attribute_id": "a%d" % i, "claim": "%s %d: как на фото" % (name, i), "reference_roi": roi}


# a0, a1 coarse; a2 fine (10000); a3 fine (3000)
INV4 = [attr(0, BIG), attr(1, BIG), attr(2, FINE_A, "заклёпка"), attr(3, FINE_B, "значок")]


def answers(*values):
    return {"answers": [{"n": n, "answer": v} for n, v in enumerate(values, 1)]}


def is_zoom(request):
    parts = parts_of(request)
    return (sum(p["type"] == "image_url" for p in parts) == 2
            and not any(p["type"] == "text" and p["text"].startswith("inventory_sha256: ") for p in parts))


def texts(request):
    return "\n".join(p["text"] for p in parts_of(request) if p["type"] == "text")


class ZoomModel:
    """A FakeModel for the three calls of the check, plus scripted replies to the zoom calls.

    A reply is an answer object (sent as the model's JSON), a str (sent as is), an exception
    (raised by the transport) or a full transport reply {"status": ...}."""

    def __init__(self, replies=(), base=None, usage=None, finish="stop"):
        self.base = base or FakeModel(inventory=INV4)
        self.replies, self.usage, self.finish = list(replies), usage, finish
        self.zoom_requests, self.headers = [], []

    def __call__(self, request, headers, timeout):
        if not is_zoom(request):
            return self.base(request, headers, timeout)
        self.zoom_requests.append(request)
        self.headers.append(headers)
        reply = self.replies[len(self.zoom_requests) - 1]
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, dict) and "status" in reply:
            return reply
        return {"status": 200, "body": chat(reply, self.usage, self.finish)}


class Runs:
    """One model per run of the controls: an inventory call starts the next one."""

    def __init__(self, models):
        self.models, self.index = list(models), -1

    def __call__(self, request, headers, timeout):
        if sum(p["type"] == "image_url" for p in parts_of(request)) == 1:
            self.index += 1
        return self.models[self.index](request, headers, timeout)


class FakeImage:
    def __init__(self, size, log, good=True):
        self.size, self.log, self.good = size, log, good

    def crop(self, box):
        self.log.append(("crop", self.size, tuple(box)))
        return FakeImage((box[2] - box[0], box[3] - box[1]), self.log, self.good)

    def convert(self, mode):
        return self

    def resize(self, size, resample):
        return FakeImage(tuple(size), self.log, self.good)

    def save(self, out, format):
        out.write(png(*self.size) if self.good else b"not an image")


class FakeImaging:
    """What the zoom uses of PIL.Image: open, new, LANCZOS; writes header-only PNGs."""
    LANCZOS = 1

    def __init__(self, good=True):
        self.log, self.good = [], good

    def open(self, stream):
        head = sniff_image(stream.read())
        return FakeImage((head["width"], head["height"]), self.log)

    def new(self, mode, size, color):
        return FakeImage(tuple(size), [], self.good)


def run_main(rows, models, mode="ask", args=("--repeats", "1"), imaging=None, environ=ENV, codes=True):
    """vision_controls._main with --zoom over the real chain; -> (exit code, stdout, codes text, log text)."""
    transport = Runs(models)
    with tempfile.TemporaryDirectory() as d:
        manifest, path = os.path.join(d, "privatemanifest.json"), os.path.join(d, "privatecodes.txt")
        with open(manifest, "w") as f:
            json.dump(rows, f)
        argv = [manifest, *args, "--zoom", mode] + (["--codes", path] if codes else [])
        out, logs = io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        root = logging.getLogger()
        level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            with contextlib.redirect_stdout(out):
                code = vision_controls._main(argv, environ, read=lambda p: JPEG, transport=transport,
                                             imaging=imaging)
        finally:
            root.removeHandler(handler)
            root.setLevel(level)
        text = ""
        if codes and os.path.exists(path):
            with open(path) as f:
                text = f.read()
        return code, out.getvalue(), text, logs.getvalue()


def zoom_lines(text):
    return [line for line in text.splitlines() if "zoom_codes" in line]


ROW = [{"kind": "different", "ref": "privateref", "cand": "privatecand"}]


def checked(inv, per_order=((None, ()), (None, ())), boxes=None):
    """(validated, first, second, record) of a check; `boxes` = per order {attribute_id: candidate_roi}."""
    v = vision_core.parse_inventory(chat({"attributes": inv}))
    parsed = []
    for index, (overrides, unmapped) in enumerate(per_order):
        answer = compare_answer(v["sha256"], overrides, unmapped, attrs=inv)
        for item in answer["results"]:
            if boxes and item["attribute_id"] in boxes[index]:
                item["candidate_roi"] = boxes[index][item["attribute_id"]]
        parsed.append(vision_core.parse_compare(chat(answer), v))
    return v, parsed[0], parsed[1], vision_core.code_record(v, *parsed)


class TargetTests(unittest.TestCase):
    """Acceptance 1: the zoom asks only for fine confirmed attributes."""

    def test_only_fine_and_confirmed_smallest_box_first(self):
        inv = INV4 + [attr(4, FINE_B, "шов")]
        weak = ({"a4": ("MATCH", "PARTIAL")}, ())             # fine but not confirmed
        v, first, second, record = checked(inv, (weak, weak))
        self.assertEqual([(a["fine"], a["cls"]) for a in record["attrs"]],
                         [(False, "confirmed"), (False, "confirmed"), (True, "confirmed"), (True, "confirmed"),
                          (True, "fine_detail_unverified")])
        targets = vz.find_targets(v, first, second, record)
        self.assertEqual([t["pos"] for t in targets], [4, 3])
        self.assertEqual([t["reference_roi"] for t in targets], [FINE_B, FINE_A])
        self.assertEqual(targets[0]["claim"], INV4[3]["claim"])

    def test_no_target_when_a_fine_attribute_differs_or_is_coarse(self):
        diff = ({"a2": ("MISMATCH", "DIRECT"), "a3": ("MISMATCH", "NONE")}, ())
        v, first, second, record = checked(INV4, (diff, diff))
        self.assertEqual(vz.find_targets(v, first, second, record), [])
        v, first, second, record = checked([attr(0, BIG), attr(1, BIG), attr(2, BIG)])
        self.assertEqual(vz.find_targets(v, first, second, record), [])

    def test_fine_boundary_is_the_cores(self):
        edge = [attr(0, BIG), attr(1, [0, 0, 100, 200]), attr(2, [0, 0, 100, 199])]   # 20000 is not fine
        v, first, second, record = checked(edge)
        self.assertEqual([t["pos"] for t in vz.find_targets(v, first, second, record)], [3])

    def test_candidate_box_is_the_union_of_both_orders(self):
        boxes = [{"a3": [100, 200, 50, 50]}, {"a3": [120, 180, 60, 40]}]
        v, first, second, record = checked(INV4, boxes=boxes)
        target = vz.find_targets(v, first, second, record)[0]
        self.assertEqual(target["candidate_roi"], [100, 180, 80, 70])
        self.assertEqual(vz.union_box([1, 2, 3, 4], None), [1, 2, 3, 4])
        self.assertEqual(vz.union_box("x", (5, 6, 7, 8)), [5, 6, 7, 8])
        self.assertIsNone(vz.union_box(None, [1, 2, True, 4]))
        self.assertIsNone(vz.union_box([1, 2, 3], [1, 2, 3, 4.0]))
        self.assertEqual(vz.union_box([0, 0, 10, 10], [5, 5, 10, 20]), [0, 0, 15, 25])
        self.assertEqual(vz.union_box([5, 5, 10, 20], [0, 0, 10, 10]), [0, 0, 15, 25])

    def test_ties_keep_inventory_order(self):
        inv = [attr(0, BIG), attr(1, FINE_A), attr(2, FINE_A), attr(3, FINE_B)]
        v, first, second, record = checked(inv)
        self.assertEqual([t["pos"] for t in vz.find_targets(v, first, second, record)], [4, 2, 3])


class RuleTests(unittest.TestCase):
    """Scope 2 and the contract: only a fine confirmed attribute moves, and only down."""

    def record(self):
        return checked(INV4)[3]

    def test_the_three_answers(self):
        record = self.record()
        for letter, cls, status in (("S", "confirmed", "MATCH"), ("A", "differs", "MISMATCH"),
                                    ("T", "fine_detail_unverified", "NEEDS_REVIEW"),
                                    ("E", "confirmed", "MATCH"), ("K", "confirmed", "MATCH")):
            classes = vz.zoomed_classes(record, {4: letter})
            self.assertEqual(classes, ["confirmed", "confirmed", "confirmed", cls], letter)
            self.assertEqual(vz.zoomed_status(vision_core, record, {4: letter}), status, letter)
        self.assertEqual(vz.zoomed_status(vision_core, record, {}), record["a"])
        self.assertEqual(vz.zoomed_status(vision_core, record, {3: "T", 4: "A"}), "MISMATCH")
        self.assertEqual(vz.ANSWER_CODE, {S: "S", A: "A", T: "T"})
        self.assertEqual(vz.ZOOM_CLASS, {"A": "differs", "T": "fine_detail_unverified"})

    def test_a_coarse_or_unconfirmed_attribute_never_changes(self):
        record = self.record()
        self.assertEqual(vz.zoomed_classes(record, {1: "A", 2: "T"}), ["confirmed"] * 4)
        weak = ({"a3": ("MATCH", "PARTIAL")}, ())
        record = checked(INV4, (weak, weak))[3]
        self.assertEqual(vz.zoomed_classes(record, {4: "A"})[3], "fine_detail_unverified")
        self.assertEqual(vz.zoomed_status(vision_core, record, {4: "A"}), "NEEDS_REVIEW")

    def test_the_extra_element_outcome_is_kept(self):
        record = dict(self.record(), extra="unconfirmed")
        self.assertEqual(vz.zoomed_status(vision_core, record, {4: "S"}), "NEEDS_REVIEW")
        record = dict(self.record(), extra="confirmed")
        self.assertEqual(vz.zoomed_status(vision_core, record, {4: "S"}), "MISMATCH")

    def test_the_status_never_gets_better_on_random_records(self):
        rng = random.Random(505)
        classes = list(vision_core.CODE_CLASS)
        for _ in range(3000):
            n = rng.randint(1, 12)
            record = {"extra": rng.choice(["none", "none", "unconfirmed", "confirmed"]),
                      "attrs": [{"pos": i, "fine": rng.random() < 0.5,
                                 "cls": rng.choice(classes + ["confirmed"] * 6)} for i in range(1, n + 1)]}
            letters = {i: rng.choice("SATEK") for i in range(1, n + 1) if rng.random() < 0.8}
            before = [a["cls"] for a in record["attrs"]]
            after = vz.zoomed_classes(record, letters)
            for a, old, new in zip(record["attrs"], before, after):
                if new != old:
                    self.assertTrue(a["fine"] and old == "confirmed")
                    self.assertEqual(new, vz.ZOOM_CLASS[letters[a["pos"]]])
            base = vision_core.status_of(before, record["extra"])
            self.assertGreaterEqual(ORDER[vz.zoomed_status(vision_core, record, letters)], ORDER[base])


class AskRunTests(unittest.TestCase):
    """Acceptance 1 through `vision_controls --zoom ask` and the real chain."""

    def test_the_three_answers_through_the_controls(self):
        rows = [{"kind": "different", "ref": "r%d" % i, "cand": "c%d" % i} for i in range(3)]
        models = [ZoomModel([answers(S, S)]), ZoomModel([answers(A, S)]), ZoomModel([answers(S, T)])]
        code, out, text, logs = run_main(rows, models)
        self.assertEqual(zoom_lines(text), [
            "kind=different zoom_codes v=1 row=0 mode=ask a=MATCH z=MATCH calls=1 codes=3S,4S",
            "kind=different zoom_codes v=1 row=1 mode=ask a=MATCH z=MISMATCH calls=1 codes=3S,4A",
            "kind=different zoom_codes v=1 row=2 mode=ask a=MATCH z=NEEDS_REVIEW calls=1 codes=3T,4S"])
        self.assertIn("  different runs=3 MATCH=3->1 MISMATCH=0->1 NEEDS_REVIEW=0->1 UNAVAILABLE=0->0", out)
        self.assertIn("  false MATCH on negatives: 3 -> 1", out)
        self.assertIn("    row=1 kind=different runs=1 MATCH=1->0", out)
        self.assertIn("  answers: PRESENT_SAME=4 ABSENT_OR_DIFFERENT=1 CANNOT_TELL=1 bad=0 over_cap=0", out)
        self.assertIn("  zoom errors by code: none", out)
        # the production numbers of the run are untouched
        self.assertIn("false MATCH on negatives: 3\n", out)
        self.assertIn("different runs=3 MATCH=100.0%", out)
        self.assertEqual(code, 1)
        self.assertEqual([len(m.base.requests) for m in models], [3, 3, 3])

    def test_the_request_lists_only_fine_confirmed_attributes(self):
        inv = INV4 + [attr(4, FINE_B, "шов")]
        weak = ({"a4": ("MATCH", "PARTIAL")}, ())
        model = ZoomModel([answers(S, S)], base=FakeModel(inventory=inv, per_order=(weak, weak)))
        code, out, text, logs = run_main(ROW, [model])
        self.assertEqual(len(model.zoom_requests), 1)
        request = model.zoom_requests[0]
        details = json.loads(next(p["text"] for p in parts_of(request)
                                  if p["type"] == "text" and p["text"].startswith("details: "))[9:])
        self.assertEqual(details, [
            {"n": 1, "detail": INV4[3]["claim"], "reference_box": FINE_B, "candidate_box": CAND},
            {"n": 2, "detail": INV4[2]["claim"], "reference_box": FINE_A, "candidate_box": CAND}])
        for other in (inv[0], inv[1], inv[4]):
            self.assertNotIn(other["claim"], texts(request))
        self.assertEqual(zoom_lines(text), ["kind=different zoom_codes v=1 row=0 mode=ask a=NEEDS_REVIEW "
                                            "z=NEEDS_REVIEW calls=1 codes=3S,4S"])

    def test_request_shape_and_ceiling_on_the_wire(self):
        model = ZoomModel([answers(S, S)])
        run_main(ROW, [model])
        request = model.zoom_requests[0]
        self.assertEqual(request["max_tokens"], vz.MAX_OUTPUT_TOKENS["ask"])
        self.assertEqual((request["temperature"], request["response_format"], request["provider"],
                          request["reasoning"], request["usage"], request["model"]),
                         (0, {"type": "json_object"}, {"data_collection": "deny"}, {"effort": "minimal"},
                          {"include": True}, ENV["VISION_MODEL"]))
        images = [p["image_url"]["url"] for p in parts_of(request) if p["type"] == "image_url"]
        self.assertEqual(images, [vision_request.data_url(JPEG)] * 2)      # the same two photos
        labels = [p["text"] for p in parts_of(request) if p["type"] == "text" and p["text"].startswith("Image ")]
        self.assertEqual(labels, ["Image 1: role=reference", "Image 2: role=candidate"])
        self.assertEqual(model.headers, [{"Authorization": "Bearer " + KEY}])   # through the real call_provider
        self.assertLessEqual(len(texts(request).encode("utf-8")), vz.MAX_PROMPT_BYTES["ask"])

    def test_reference_and_candidate_are_not_swapped(self):
        imaging = FakeImaging()
        model = ZoomModel([answers(S, S), {"answer": S}, {"answer": S}])
        with tempfile.TemporaryDirectory() as d:
            manifest = os.path.join(d, "m.json")
            with open(manifest, "w") as f:
                json.dump(ROW, f)
            with contextlib.redirect_stdout(io.StringIO()):
                vision_controls._main([manifest, "--repeats", "1", "--zoom", "both"], ENV, imaging=imaging,
                                      read=lambda p: JPEG if p == "privateref" else PNG, transport=Runs([model]))
        images = [p["image_url"]["url"] for p in parts_of(model.zoom_requests[0]) if p["type"] == "image_url"]
        self.assertEqual(images, [vision_request.data_url(JPEG), vision_request.data_url(PNG)])
        # crop: the reference photo (1280 x 960) is cut first, then the candidate photo (960 x 1280)
        self.assertEqual([entry[1] for entry in imaging.log[-4:]], [(1280, 960), (960, 1280)] * 2)

    def test_only_a_completed_check_is_zoomed(self):
        recorder = vz.RecordingCore(vision_core)
        v, first, second, record = checked(INV4)
        calls = []

        def provider(request, transport, environ, logger):
            calls.append(request)
            return chat(answers(A, A))
        for status, expected in (("UNAVAILABLE", 0), ("SOMETHING", 0), ("MISMATCH", 0), ("MATCH", 1)):
            def compare(reference, candidate):
                recorder.assess(v, first, second)
                return {"status": status}
            zoom = vz.Zoom(("ask",), recorder, provider, None, ENV, "m/x")
            del calls[:]
            self.assertEqual(zoom.wrap(compare)(JPEG, PNG), {"status": status})
            self.assertEqual(len(calls), expected, status)
            run = zoom.close_run("different", 0, status)
            self.assertEqual(run["modes"] is not None, status in ("MISMATCH", "MATCH"))
        self.assertEqual(run["modes"]["ask"]["status"], "MISMATCH")
        self.assertIsNone(zoom.close_run("different", 1, "MATCH")["modes"])      # nothing is carried over

    def test_a_mismatch_or_a_check_without_fine_confirmed_is_not_zoomed(self):
        diff = ({"a0": ("MISMATCH", "DIRECT")}, ())
        coarse = [attr(0, BIG), attr(1, BIG), attr(2, BIG)]
        models = [ZoomModel(base=FakeModel(inventory=INV4, per_order=(diff, diff))),
                  ZoomModel(base=FakeModel(inventory=coarse))]
        rows = ROW + [{"kind": "same", "ref": "x"}]
        code, out, text, logs = run_main(rows, models)
        self.assertEqual([len(m.zoom_requests) for m in models], [0, 0])
        self.assertEqual(zoom_lines(text), [
            "kind=different zoom_codes v=1 row=0 mode=ask a=MISMATCH z=MISMATCH calls=0 codes=-",
            "kind=same zoom_codes v=1 row=1 mode=ask a=MATCH z=MATCH calls=0 codes=-"])
        self.assertIn("  extra calls per check, all completed checks (n=2): min=0 p50=0 p95=0 max=0", out)
        self.assertIn("  extra calls per check, checks that are MATCH without zoom (n=1): min=0", out)

    def test_a_needs_review_check_is_zoomed(self):
        weak = ({"a0": ("MATCH", "PARTIAL")}, ())
        model = ZoomModel([answers(A, S)], base=FakeModel(inventory=INV4, per_order=(weak, weak)))
        code, out, text, logs = run_main(ROW, [model])
        self.assertEqual(zoom_lines(text), ["kind=different zoom_codes v=1 row=0 mode=ask a=NEEDS_REVIEW "
                                            "z=MISMATCH calls=1 codes=3S,4A"])
        self.assertIn("  extra per check, checks that are MATCH without zoom (n=0): n/a", out)

    def test_an_unavailable_run_is_not_zoomed_and_counts_as_it_was(self):
        broken = ZoomModel(base=FakeModel(inventory=INV4, fail_at=1, reply={"status": 500, "body": None}))
        code, out, text, logs = run_main(ROW * 2, [broken, ZoomModel([answers(A, A)])])
        self.assertEqual(text.splitlines()[0], "kind=different unavailable")
        self.assertEqual(zoom_lines(text), ["kind=different zoom_codes v=1 row=1 mode=ask a=MATCH z=MISMATCH "
                                            "calls=1 codes=3A,4A"])
        self.assertIn("  different runs=2 MATCH=1->0 MISMATCH=0->1 NEEDS_REVIEW=0->0 UNAVAILABLE=1->1", out)
        self.assertIn("  extra calls per check, all completed checks (n=1): min=1 p50=1 p95=1 max=1", out)
        self.assertEqual(len(broken.zoom_requests), 0)

    def test_repeats_share_the_row_index(self):
        models = [ZoomModel([answers(A, S)]), ZoomModel([answers(S, S)]), ZoomModel([answers(S, T)]),
                  ZoomModel([answers(S, S)])]
        rows = ROW + [{"kind": "same", "ref": "x"}]
        code, out, text, logs = run_main(rows, models, args=("--repeats", "2"))
        self.assertEqual([line.split()[3] for line in zoom_lines(text)], ["row=0", "row=0", "row=1", "row=1"])
        self.assertIn("    row=0 kind=different runs=2 MATCH=2->1", out)
        self.assertIn("    row=1 kind=same runs=2 MATCH=2->1", out)
        self.assertIn("  same runs=2 MATCH=2->1 MISMATCH=0->0 NEEDS_REVIEW=0->1 UNAVAILABLE=0->0", out)
        self.assertIn("  recrop runs=0 MATCH=0->0", out)
        self.assertIn("  positive runs=2 MATCH=2->1 MISMATCH=0->0 NEEDS_REVIEW=0->1 UNAVAILABLE=0->0", out)
        self.assertIn("  positive MATCH share: 100.0% -> 50.0%", out)

    def test_the_codes_file_still_recomputes(self):
        code, out, text, logs = run_main(ROW, [ZoomModel([answers(A, S)])])
        self.assertEqual(text.splitlines()[0], "kind=different check_codes v=3 a=MATCH n=4 "
                                               "codes=1MDD011C,2MDD011C,3MDD111C,4MDD111C;n")
        result = recompute.recompute(text.splitlines())
        self.assertEqual((result["lines"], result["bad"], result["a_diff"], result["inconsistent"]), (1, 0, 0, 0))

    def test_without_a_codes_file(self):
        code, out, text, logs = run_main(ROW, [ZoomModel([answers(A, S)])], codes=False)
        self.assertIn("  false MATCH on negatives: 1 -> 0", out)


class BadAnswerTests(unittest.TestCase):
    """Acceptance 1: a bad zoom answer leaves the attribute unchanged and is counted."""

    def one(self, reply, finish="stop"):
        model = ZoomModel([reply], finish=finish)
        code, out, text, logs = run_main(ROW, [model])
        return zoom_lines(text)[0].split("mode=ask ")[1], out

    def test_call_level_failures(self):
        for reply, finish, error in (("no json here", "stop", "NOT_JSON"), ([1, 2], "stop", "NOT_OBJECT"),
                                     ({"status": 500, "body": None}, "stop", "HTTP_ERROR"),
                                     (TimeoutError("t"), "stop", "TIMEOUT"),
                                     ({"status": 200, "body": "x"}, "stop", "MALFORMED_BODY"),
                                     (answers(A, A), "length", "CUT_OFF"), ("", "stop", "EMPTY_ANSWER")):
            with self.subTest(error=error):
                line, out = self.one(reply, finish)
                self.assertEqual(line, "a=MATCH z=MATCH calls=1 codes=3E,4E")
                self.assertIn("  zoom errors by code:\n    code=%s n=1\n" % error, out)
                self.assertIn("  answers: PRESENT_SAME=0 ABSENT_OR_DIFFERENT=0 CANNOT_TELL=0 bad=2 over_cap=0", out)
                self.assertIn("  false MATCH on negatives: 1 -> 1", out)

    def test_item_level_failures_keep_the_good_items(self):
        cases = [({"answers": [{"n": 1, "answer": A}]}, "3E,4A", 1),                       # n=2 missing
                 ({"answers": [{"n": 1, "answer": "MAYBE"}, {"n": 2, "answer": T}]}, "3T,4E", 1),
                 ({"answers": [{"n": 1, "answer": S}, {"n": 1, "answer": A}, {"n": 2, "answer": A}]}, "3A,4E", 1),
                 ({"answers": [{"n": True, "answer": A}, {"n": "2", "answer": A}, "x", {"n": 9, "answer": A}]},
                  "3E,4E", 2),
                 ({"answers": [{"n": 1, "answer": ["ABSENT_OR_DIFFERENT"]}, {"n": 2, "answer": None}]}, "3E,4E", 2),
                 ({"answers": {"n": 1}}, "3E,4E", 2), ({"other": 1}, "3E,4E", 2),
                 ({"answers": [{"n": 1, "answer": "absent_or_different"}, {"n": 2, "answer": S}]}, "3S,4E", 1)]
        for reply, codes, bad in cases:
            with self.subTest(codes=codes, reply=reply):
                line, out = self.one(reply)
                self.assertTrue(line.endswith("calls=1 codes=" + codes), line)
                self.assertIn("    code=BAD_ITEM n=%d\n" % bad, out)
                self.assertIn(" bad=%d " % bad, out)

    def test_a_cost_is_counted_only_when_reported(self):
        model = ZoomModel([{"status": 500, "body": None}])
        code, out, text, logs = run_main(ROW, [model])
        self.assertIn("  zoom calls=1 total cost USD=0.000000 calls without a reported cost=1", out)
        for usage in ({"cost": True}, {"cost": "0.5"}, {"cost": float("nan")}, None):
            code, out, text, logs = run_main(ROW, [ZoomModel([answers(S, S)], usage=usage)])
            self.assertIn("  zoom calls=1 total cost USD=0.000000 calls without a reported cost=1", out)

    def test_a_prompt_over_the_bound_is_not_sent(self):
        inv = [attr(0, BIG), attr(1, BIG)] + [
            {"attribute_id": "f%d" % i, "claim": "очень длинное описание детали " * 6 + ": %d" % i,
             "reference_roi": FINE_B} for i in range(10)]
        v, first, second, record = checked(inv)
        targets = vz.find_targets(v, first, second, record)
        self.assertEqual(len(targets), 10)
        with self.assertRaises(AnswerError) as cm:
            vz.build_ask_request(targets, JPEG, PNG, "m/x")
        self.assertEqual(cm.exception.args[0], "PROMPT_TOO_LONG")
        zoom = vz.Zoom(("ask",), vz.RecordingCore(vision_core), None, None, ENV, "m/x")
        tally = {"calls": 0, "cost": 0.0, "unknown": 0}
        self.assertEqual(zoom._ask(JPEG, PNG, targets, tally), ["E"] * 10)
        self.assertEqual((tally["calls"], zoom.errors), (0, {"ask": {"PROMPT_TOO_LONG": 1}}))

    def test_a_crash_inside_the_zoom_never_changes_the_run(self):
        model = ZoomModel([answers(A, A)])
        with patch.object(vz, "find_targets", side_effect=RuntimeError(MARK)):
            code, out, text, logs = run_main(ROW, [model])
        self.assertEqual(text.splitlines(), ["kind=different check_codes v=3 a=MATCH n=4 "
                                             "codes=1MDD011C,2MDD011C,3MDD111C,4MDD111C;n"])
        self.assertIn("  different runs=1 MATCH=1->1", out)
        self.assertIn("    code=ZOOM_FAILED n=1", out)
        self.assertNotIn(MARK, out + logs)


class LeakTests(unittest.TestCase):
    """Acceptance 1: nothing is logged or printed beyond codes."""

    def test_no_claim_id_box_path_key_or_model_text(self):
        inv = [{"attribute_id": MARK + ".id%d" % i, "claim": MARK + " claim %d: x" % i,
                "reference_roi": [611, 733, 97, 89] if i > 1 else BIG} for i in range(4)]
        reply = {"answers": [{"n": 1, "answer": A, "note": MARK}, {"n": 2, "answer": MARK + "TEXT"}],
                 "comment": MARK}
        for mode, replies in (("ask", [reply]), ("crop", [{"answer": MARK}, {"answer": A, "why": MARK}])):
            model = ZoomModel(replies, base=FakeModel(inventory=inv),
                              usage={"cost": 0.0011, "prompt_tokens": 5120, "completion_tokens": 33})
            code, out, text, logs = run_main(ROW, [model], mode=mode, imaging=FakeImaging())
            self.assertEqual(len(zoom_lines(text)), 1)
            self.assertEqual(len(model.zoom_requests), len(replies))
            for banned in (MARK, "private", "611", "733", KEY, "claim", "base64", "SECRETJPEGBYTES"):
                self.assertNotIn(banned, out + text + logs, (mode, banned))
            self.assertIn("zoom_call mode=%s usage_prompt=5120 usage_completion=33 cost=0.0011" % mode, logs)
            for line in zoom_lines(text):
                self.assertRegex(line, r"^kind=different zoom_codes v=1 row=\d+ mode=(ask|crop) a=[A-Z_]+ "
                                       r"z=[A-Z_]+ calls=\d+ codes=(-|\d{1,2}[SATEK](,\d{1,2}[SATEK])*)$")

    def test_provider_failure_logs_a_fixed_code_only(self):
        model = ZoomModel([RuntimeError(MARK)])
        code, out, text, logs = run_main(ROW, [model])
        self.assertIn("zoom_unavailable mode=ask code=HTTP_ERROR", logs)
        self.assertNotIn(MARK, out + text + logs)
        self.assertEqual(vz._code(MARK + " lower"), "UNKNOWN")
        self.assertEqual(vz._code("TIMEOUT"), "TIMEOUT")

    def test_a_provider_error_text_never_reaches_the_log(self):
        from app.photo_compare import ProviderUnavailable

        def provider(request, transport, environ, logger):
            raise ProviderUnavailable(MARK + " text")
        zoom = vz.Zoom(("ask",), vz.RecordingCore(vision_core), provider, None, ENV, "m/x")
        tally = {"calls": 0, "cost": 0.0, "unknown": 0}
        with self.assertLogs("vision.zoom", "WARNING") as captured:
            self.assertIsNone(zoom._call("ask", {}, tally))
        self.assertEqual(captured.output, ["WARNING:vision.zoom:zoom_unavailable mode=ask code=UNKNOWN"])
        self.assertEqual((tally, zoom.errors), ({"calls": 1, "cost": 0.0, "unknown": 1}, {"ask": {"UNKNOWN": 1}}))

    def test_a_bad_answer_logs_a_fixed_code_only(self):
        code, out, text, logs = run_main(ROW, [ZoomModel([MARK + " not json"])])
        self.assertIn("zoom_bad_answer mode=ask code=NOT_JSON", logs)
        self.assertNotIn(MARK, out + text + logs)


class CropGeometryTests(unittest.TestCase):
    def test_box_is_padded_square_and_inside(self):
        # 100 x 100 permille of 1000 x 1000 at (500, 600): 100 px box, padded to 200 px, centred
        self.assertEqual(vz.crop_box([500, 600, 100, 100], 1000, 1000), (450, 550, 650, 750))
        # a wide box: the longer side decides, the square is centred on the box
        self.assertEqual(vz.crop_box([400, 500, 200, 20], 1000, 1000), (300, 310, 700, 710))
        # near the corner the square is moved inside, not cut
        self.assertEqual(vz.crop_box([0, 0, 100, 100], 1000, 1000), (0, 0, 200, 200))
        self.assertEqual(vz.crop_box([900, 900, 100, 100], 1000, 1000), (800, 800, 1000, 1000))
        # a tiny box is cut at least CROP_MIN_PX wide
        self.assertEqual(vz.crop_box([500, 500, 10, 10], 1000, 1000), (473, 473, 537, 537))
        # a strip across a landscape photo: the square cannot exceed the image
        self.assertEqual(vz.crop_box([0, 480, 1000, 19], 1280, 960), (0, 0, 1280, 960))
        # permille of each side separately
        self.assertEqual(vz.crop_box([500, 500, 100, 100], 2000, 1000), (900, 350, 1300, 750))
        self.assertEqual((vz.CROP_PAD, vz.CROP_MIN_PX, vz.CROP_SIDE), (0.5, 64, 768))

    def test_box_always_inside_and_holds_the_roi(self):
        rng = random.Random(77)
        for _ in range(3000):
            width, height = rng.randint(64, 1536), rng.randint(64, 1536)
            w, h = rng.randint(10, 1000), rng.randint(10, 1000)
            x, y = rng.randint(0, 1000 - w), rng.randint(0, 1000 - h)
            left, top, right, bottom = vz.crop_box([x, y, w, h], width, height)
            self.assertTrue(0 <= left < right <= width and 0 <= top < bottom <= height)
            if max(w * width, h * height) * 2 <= 1000 * min(width, height):   # the padded square fits
                self.assertLessEqual(left, x * width / 1000)
                self.assertGreaterEqual(right, (x + w) * width / 1000)
                self.assertLessEqual(top, y * height / 1000)
                self.assertGreaterEqual(bottom, (y + h) * height / 1000)

    def test_output_size_has_the_longer_side_at_crop_side(self):
        self.assertEqual(vz.crop_size(200, 200), (768, 768))
        self.assertEqual(vz.crop_size(64, 64), (768, 768))
        self.assertEqual(vz.crop_size(1280, 960), (768, 576))
        self.assertEqual(vz.crop_size(960, 1280), (576, 768))
        self.assertEqual(vz.crop_size(100, 1), (768, 8))
        self.assertEqual(vz.crop_size(3000, 1), (768, 1))
        for w, h in ((1, 1), (5, 900), (1536, 1535), (333, 334)):
            self.assertEqual(max(vz.crop_size(w, h)), 768)

    def test_crop_png_cuts_and_scales(self):
        imaging = FakeImaging()
        data = vz.crop_png(imaging, JPEG, [500, 600, 100, 100])       # JPEG is 1280 x 960
        self.assertEqual(imaging.log, [("crop", (1280, 960), (576, 496, 832, 752))])
        self.assertEqual(sniff_image(data), {"format": "png", "width": 768, "height": 768})

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is not installed here")
    def test_crop_png_with_the_real_pillow(self):
        imaging = vz.load_imaging()
        vz.imaging_selftest(imaging)
        source = io.BytesIO()
        picture = imaging.new("RGB", (1000, 800), (10, 20, 30))
        picture.paste((250, 0, 0), (500, 480, 600, 560))             # the "detail": a red patch
        picture.save(source, format="PNG")
        data = vz.crop_png(imaging, source.getvalue(), [500, 600, 100, 100])
        self.assertEqual(sniff_image(data), {"format": "png", "width": 768, "height": 768})
        crop = imaging.open(io.BytesIO(data))
        self.assertEqual(crop.getpixel((384, 384)), (250, 0, 0))     # the detail is in the middle
        self.assertEqual(crop.getpixel((20, 20)), (10, 20, 30))      # with its surroundings
        vision_request.data_url(data)


class CropRunTests(unittest.TestCase):
    def test_one_call_per_attribute_two_crops_each(self):
        imaging = FakeImaging()
        model = ZoomModel([{"answer": A}, {"answer": S}])
        code, out, text, logs = run_main(ROW, [model], mode="crop", imaging=imaging)
        self.assertEqual(zoom_lines(text), ["kind=different zoom_codes v=1 row=0 mode=crop a=MATCH z=MISMATCH "
                                            "calls=2 codes=3S,4A"])
        self.assertEqual(len(model.zoom_requests), 2)
        for request, claim in zip(model.zoom_requests, (INV4[3]["claim"], INV4[2]["claim"])):   # smallest first
            self.assertEqual(request["max_tokens"], vz.MAX_OUTPUT_TOKENS["crop"])
            images = [p["image_url"]["url"] for p in parts_of(request) if p["type"] == "image_url"]
            self.assertEqual(len(images), 2)
            self.assertTrue(all(u.startswith("data:image/png;base64,") for u in images))
            self.assertIn(json.dumps(claim, ensure_ascii=False), texts(request))
            self.assertEqual(texts(request).count("как на фото"), 1)     # one attribute per call
            self.assertLessEqual(len(texts(request).encode("utf-8")), vz.MAX_PROMPT_BYTES["crop"])
        # reference cut at the reference box, candidate at the candidate box
        self.assertEqual([entry for entry in imaging.log if entry[1] == (1280, 960)], [   # not the self-test
            ("crop", (1280, 960), vz.crop_box(FINE_B, 1280, 960)),
            ("crop", (1280, 960), vz.crop_box(CAND, 1280, 960)),
            ("crop", (1280, 960), vz.crop_box(FINE_A, 1280, 960)),
            ("crop", (1280, 960), vz.crop_box(CAND, 1280, 960))])
        self.assertNotEqual(vz.crop_box(FINE_B, 1280, 960), vz.crop_box(CAND, 1280, 960))
        self.assertIn("  extra calls per check, all completed checks (n=1): min=2 p50=2 p95=2 max=2", out)

    def test_cap_per_check_largest_boxes_are_left_out(self):
        fine = [[10, 10, 20 + 10 * i, 50] for i in range(6)]            # areas grow with i
        inv = [attr(0, BIG)] + [attr(i + 1, roi) for i, roi in enumerate(reversed(fine))]
        model = ZoomModel([{"answer": S}] * 3 + [{"answer": T}], base=FakeModel(inventory=inv))
        code, out, text, logs = run_main(ROW, [model], mode="crop", imaging=FakeImaging())
        self.assertEqual(vz.CROP_MAX_CALLS, 4)
        self.assertEqual(len(model.zoom_requests), 4)
        # positions 2 and 3 hold the two largest boxes: not asked, unchanged; position 4 is asked last
        self.assertEqual(zoom_lines(text), ["kind=different zoom_codes v=1 row=0 mode=crop a=MATCH z=NEEDS_REVIEW "
                                            "calls=4 codes=2K,3K,4T,5S,6S,7S"])
        self.assertIn(" bad=0 over_cap=2", out)

    def test_bad_crop_answers_and_failures(self):
        model = ZoomModel([{"answer": "NOPE"}, "garbage"])
        code, out, text, logs = run_main(ROW, [model], mode="crop", imaging=FakeImaging())
        self.assertTrue(zoom_lines(text)[0].endswith("a=MATCH z=MATCH calls=2 codes=3E,4E"))
        self.assertIn("    code=BAD_ITEM n=1\n    code=NOT_JSON n=1\n", out)
        self.assertEqual([vz.parse_crop(a) for a in ({"answer": T}, {"answer": 1}, {}, {"answer": [S]})],
                         ["T", "E", "E", "E"])

    def test_a_crop_that_cannot_be_cut_is_not_sent(self):
        class Breaking(FakeImaging):
            def open(self, stream):
                if self.log:                     # every crop after the self-test fails
                    raise OSError(MARK)
                return super().open(stream)
        model = ZoomModel([{"answer": A}])
        code, out, text, logs = run_main(ROW, [model], mode="crop", imaging=Breaking())
        self.assertTrue(zoom_lines(text)[0].endswith("a=MATCH z=MATCH calls=0 codes=3E,4E"))
        self.assertIn("    code=CROP_FAILED n=2", out)
        self.assertEqual(len(model.zoom_requests), 0)
        self.assertNotIn(MARK, out + logs)

    def test_a_crop_larger_than_the_bound_is_refused(self):
        target = {"claim": "x: y"}
        with self.assertRaises(ImageRejected) as cm:
            vz.build_crop_request(target, png(769, 10), png(768, 768), "m/x")
        self.assertEqual(cm.exception.args[0], "CROP_SIZE")
        with self.assertRaises(ImageRejected):
            vz.build_crop_request(target, png(768, 768), png(10, 769), "m/x")
        with self.assertRaises(ImageRejected):
            vz.build_crop_request(target, png(768, 768), b"junk", "m/x")
        with self.assertRaises(AnswerError):
            vz.build_crop_request({"claim": "д" * 800}, png(768, 768), png(768, 768), "m/x")
        vz.build_crop_request(target, png(768, 768), png(10, 768), "m/x")

    def test_a_refused_crop_request_is_counted_and_not_sent(self):
        class Big(FakeImaging):
            def open(self, stream):
                image = super().open(stream)
                if image.size == (1280, 960):            # the photos, not the self-test picture
                    image.resize = lambda size, resample: FakeImage((900, 900), [])
                    image.crop = lambda box: image
                return image
        model = ZoomModel([])
        code, out, text, logs = run_main(ROW, [model], mode="crop", imaging=Big())
        self.assertTrue(zoom_lines(text)[0].endswith("a=MATCH z=MATCH calls=0 codes=3E,4E"))
        self.assertIn("    code=CROP_SIZE n=2", out)

    def test_both_modes_share_one_check(self):
        model = ZoomModel([answers(S, S), {"answer": A}, {"answer": S}])
        code, out, text, logs = run_main(ROW, [model], mode="both", imaging=FakeImaging())
        self.assertEqual(len(model.base.requests), 3)
        self.assertEqual([line.split("v=1 ")[1] for line in zoom_lines(text)],
                         ["row=0 mode=ask a=MATCH z=MATCH calls=1 codes=3S,4S",
                          "row=0 mode=crop a=MATCH z=MISMATCH calls=2 codes=3S,4A"])
        self.assertIn("zoom mode=ask ", out)
        self.assertIn("zoom mode=crop ", out)
        self.assertLess(out.index("zoom mode=ask "), out.index("zoom mode=crop "))


class SetupTests(unittest.TestCase):
    """Acceptance 1: `crop` without Pillow exits with a clear message; no call is made."""

    def test_crop_without_pillow(self):
        model = ZoomModel([{"answer": A}])
        with patch.dict(sys.modules, {"PIL": None}):
            with self.assertRaises(vz.ZoomSetupError) as cm:
                vz.load_imaging()
            self.assertEqual(cm.exception.args[0], "PILLOW_MISSING")
            for mode in ("crop", "both"):
                code, out, text, logs = run_main(ROW, [model], mode=mode)
                self.assertEqual(code, 2)
                self.assertEqual(out, "zoom crop needs Pillow on this machine (pip install Pillow); "
                                      "no provider call was made\n")
            code, out, text, logs = run_main(ROW, [ZoomModel([answers(S, S)])], mode="ask")
            self.assertEqual(code, 1)                      # ask needs no Pillow
        self.assertEqual(len(model.base.requests) + len(model.zoom_requests), 0)

    def test_crop_with_a_pillow_that_cannot_cut(self):
        model = ZoomModel([{"answer": A}])
        code, out, text, logs = run_main(ROW, [model], mode="crop", imaging=FakeImaging(good=False))
        self.assertEqual((code, out), (2, "zoom crop: Pillow cannot cut a test crop; no provider call was made\n"))
        self.assertEqual(len(model.base.requests), 0)

        class Raising:
            def new(self, *a):
                raise RuntimeError(MARK)

        class Small(FakeImaging):                      # the crop comes out with a wrong size
            def open(self, stream):
                image = super().open(stream)
                image.crop = lambda box: image
                image.resize = lambda size, resample: FakeImage((700, 700), [])
                return image
        for broken in (Raising(), Small()):
            with self.assertRaises(vz.ZoomSetupError) as cm:
                vz.imaging_selftest(broken)
            self.assertEqual(cm.exception.args[0], "IMAGING_BROKEN")
        vz.imaging_selftest(FakeImaging())

    def test_usage(self):
        def main(*argv, **kw):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                return vision_controls._main(list(argv), ENV, **kw), out.getvalue()
        usage = "usage: --zoom needs ask, crop or both, and cannot be combined with --gate\n"
        self.assertEqual(main("m.json", "--zoom", "wide"), (2, usage))
        self.assertEqual(main("m.json", "--zoom"), (2, usage))
        self.assertEqual(main("m.json", "--zoom", "ask", "--gate"), (2, usage))
        self.assertEqual(main("m.json", "--zoom", "ask", compare=lambda a, b: {"status": "MATCH"}), (2, usage))
        code, out = main("--zoom", "ask")
        self.assertEqual(code, 2)
        self.assertIn("[--zoom ask|crop|both]", out)
        self.assertEqual(vision_controls.ZOOM_CHOICES,
                         {"ask": ("ask",), "crop": ("crop",), "both": ("ask", "crop")})

    def test_not_configured(self):
        code, out, text, logs = run_main(ROW, [], environ={})
        self.assertEqual((code, out), (2, "vision not configured\n"))


class CostTests(unittest.TestCase):
    """Acceptance 2: the worst case of one zoom call, from the constants, is within one hold."""

    def test_the_hold_is_the_spend_caps(self):
        self.assertEqual(vz.PER_CALL_HOLD_USD, vision_wiring.PER_CALL_FALLBACK_USD)
        self.assertEqual(vz.PER_CALL_HOLD_USD, 0.02)

    def test_crop_token_bound_covers_both_public_rules(self):
        def tiles(w, h):
            unit = min(768, max(256, math.floor(min(w, h) / 1.5)))
            return math.ceil(w / unit) * math.ceil(h / unit)
        side = vz.CROP_SIDE
        # for a fixed short side the tile count grows with the long side: the long side is at the limit
        worst = max(tiles(side, h) for h in range(1, side + 1)) * 258
        self.assertEqual(worst, 1548)
        self.assertLessEqual(max(worst, 1120), vz.CROP_TOKENS_MAX)
        self.assertEqual(vz.IMAGE_TOKENS, {"ask": vision_request.IMAGE_TOKENS_MAX, "crop": vz.CROP_TOKENS_MAX})
        self.assertLessEqual(vz.CROP_SIDE, vision_request.MAX_IMAGE_SIDE)

    def test_worst_zoom_call_fits_one_hold(self):
        r = vision_request
        expected = {"ask": 0.010119, "crop": 0.004422}
        for mode in vz.MODES:
            tokens_in = 2 * vz.IMAGE_TOKENS[mode] + vz.MAX_PROMPT_BYTES[mode] + r.PROMPT_OVERHEAD_TOKENS
            usd = tokens_in * r.INPUT_USD_PER_M / 1e6 + vz.MAX_OUTPUT_TOKENS[mode] * r.OUTPUT_USD_PER_M / 1e6
            self.assertAlmostEqual(usd, vz.worst_case_zoom_call_usd(mode), places=12)
            self.assertAlmostEqual(usd, expected[mode], places=9)
            self.assertLessEqual(usd, vision_wiring.PER_CALL_FALLBACK_USD)
            self.assertLessEqual(usd, 0.02)
        self.assertEqual(r.MAX_IMAGES_PER_CALL, 2)
        self.assertEqual(vz.MAX_CALLS_PER_CHECK, {"ask": 1, "crop": 4})
        self.assertEqual((vz.MAX_OUTPUT_TOKENS, vz.MAX_PROMPT_BYTES),
                         ({"ask": 600, "crop": 200}, {"ask": 4000, "crop": 1500}))

    def test_the_ceilings_are_enforced_by_the_payload(self):
        for mode in vz.MODES:
            fits = "я" * (vz.MAX_PROMPT_BYTES[mode] // 2)
            payload = vz.zoom_payload("m/x", [vision_request.text_part(fits)], mode)
            self.assertEqual(payload["max_tokens"], vz.MAX_OUTPUT_TOKENS[mode])
            self.assertLess(payload["max_tokens"], vision_request.MAX_OUTPUT_TOKENS)
            with self.assertRaises(AnswerError):
                vz.zoom_payload("m/x", [vision_request.text_part(fits + "я")], mode)
            with self.assertRaises(AnswerError):       # one byte over
                vz.zoom_payload("m/x", [vision_request.text_part(fits + "x")], mode)
            with self.assertRaises(AnswerError):       # the bound is on all text parts together
                vz.zoom_payload("m/x", [vision_request.text_part(fits), vision_request.text_part("я")], mode)
            image = vision_request.image_part(vision_request.data_url(JPEG))
            with self.assertRaises(AnswerError):
                vz.zoom_payload("m/x", [image, image, image], mode)

    def test_real_prompts_fit_the_text_bounds(self):
        inv = [attr(0, BIG)] + [
            {"attribute_id": "part%02d.detail" % i, "claim": "деталь номер %d товара: тёмно-синий цвет с отливом" % i,
             "reference_roi": FINE_B} for i in range(11)]
        v, first, second, record = checked(inv)
        targets = vz.find_targets(v, first, second, record)
        self.assertEqual(len(targets), 11)
        request = vz.build_ask_request(targets, JPEG, PNG, "m/x")
        self.assertLess(len(texts(request).encode("utf-8")), vz.MAX_PROMPT_BYTES["ask"])
        request = vz.build_crop_request(targets[0], png(768, 768), png(768, 500), "m/x")
        self.assertLess(len(texts(request).encode("utf-8")), vz.MAX_PROMPT_BYTES["crop"])

    def test_the_report_prints_the_worst_case_against_the_hold(self):
        usage = {"cost": 0.0015, "prompt_tokens": 100, "completion_tokens": 9}
        models = [ZoomModel([answers(S, S)], usage=usage), ZoomModel([answers(S, S)], usage={"cost": 0.0025}),
                  ZoomModel([answers(S, S)])]
        code, out, text, logs = run_main(ROW * 3, models)
        self.assertIn("  worst case of one zoom call: 0.010119 USD (within the per-call hold 0.02 USD); "
                      "at most 1 call(s) per check = 0.010119 USD", out)
        self.assertIn("  extra cost per check USD, all completed checks (n=3): p50=0.001500 p95=0.002500 "
                      "max=0.002500", out)
        self.assertIn("  extra cost per check USD, checks that are MATCH without zoom (n=3): p50=0.001500", out)
        self.assertIn("  zoom calls=3 total cost USD=0.004000 calls without a reported cost=1 "
                      "(not in the cost numbers)", out)
        code, out, text, logs = run_main(ROW, [ZoomModel([{"answer": S}] * 2)], mode="crop", imaging=FakeImaging())
        self.assertIn("  worst case of one zoom call: 0.004422 USD (within the per-call hold 0.02 USD); "
                      "at most 4 call(s) per check = 0.017688 USD", out)
        with patch.dict(vz.MAX_OUTPUT_TOKENS, {"ask": 6000}):
            zoom = vz.Zoom(("ask",), None, None, None, ENV, "m/x")
            self.assertIn("(ABOVE the per-call hold 0.02 USD)", vz.report(zoom)[-1])

    def test_percentile_is_nearest_rank(self):
        values = [3, 1, 2, 5, 4]
        self.assertEqual([vz.percentile(values, p) for p in (0, 1, 20, 21, 50, 95, 100)], [1, 1, 1, 2, 3, 5, 5])
        self.assertEqual(vz.percentile([7], 50), 7)
        self.assertEqual(vz.percentile(list(range(1, 21)), 95), 19)
        self.assertEqual(vz.percentile(list(range(1, 22)), 95), 20)
        self.assertEqual(vz.percentile([1, 2], 50), 1)


class ProductionPathTests(unittest.TestCase):
    """Acceptance 1: the production path behaves the same; the bot does not know the zoom."""

    def test_recording_core_gives_the_same_check(self):
        weak = ({"a3": ("MATCH", "PARTIAL")}, ())
        for per_order in (((None, ()), (None, ())), (weak, weak), (({"a0": ("MISMATCH", "DIRECT")}, ()),) * 2):
            plain = FakeModel(inventory=INV4, per_order=per_order)
            recorded = FakeModel(inventory=INV4, per_order=per_order)
            got_plain, got_recorded = [], []
            recorder = vz.RecordingCore(vision_core)
            with self.assertLogs("vision", "INFO") as log_plain:
                a = vision_compare.compare_from_env(ENV, transport=plain, on_codes=got_plain.append)(JPEG, PNG)
            with self.assertLogs("vision", "INFO") as log_recorded:
                b = vision_compare.compare_from_env(ENV, transport=recorded, core=recorder,
                                                    on_codes=got_recorded.append)(JPEG, PNG)
            self.assertEqual(a, b)
            self.assertEqual(plain.requests, recorded.requests)
            self.assertEqual(got_plain, got_recorded)
            self.assertEqual(log_plain.output, log_recorded.output)
            validated, first, second = recorder.take()
            self.assertEqual(vision_core.assess(validated, first, second), a)
            self.assertIsNone(recorder.take())                 # handed out once

    def test_the_default_chain_does_not_use_the_recorder(self):
        self.assertIs(vision_compare._default_core(), vision_core)
        self.assertIs(vz.RecordingCore(vision_core).rule_a_prime, vision_core.rule_a_prime)

    def test_only_the_controls_tool_imports_the_zoom(self):
        users = sorted(p.name for p in (ROOT / "app").rglob("*.py")
                       if "vision_controls_zoom" in p.read_text(encoding="utf-8"))
        self.assertEqual(users, ["vision_controls.py", "vision_controls_zoom.py"])
        source = (ROOT / "app" / "vision_controls.py").read_text(encoding="utf-8")
        head = source.split("\ndef ", 1)[0].split('"""', 2)[2]             # module-level imports
        self.assertNotIn("zoom", head)
        for name in ("Dockerfile", ".github/workflows/ci.yml", ".github/workflows/deploy.yml"):
            self.assertNotIn("pillow", (ROOT / name).read_text(encoding="utf-8").lower())

    def test_a_run_without_zoom_makes_no_extra_call_and_prints_no_zoom(self):
        model = ZoomModel([answers(A, A)])
        with tempfile.TemporaryDirectory() as d:
            manifest = os.path.join(d, "m.json")
            with open(manifest, "w") as f:
                json.dump(ROW, f)
            out = io.StringIO()

            def fake(environ, **kw):
                return vision_compare.compare_from_env(environ, transport=model, **kw)
            with contextlib.redirect_stdout(out):
                code = vision_controls._main([manifest, "--repeats", "1"], ENV, read=lambda p: JPEG, make=fake)
        self.assertEqual((len(model.base.requests), len(model.zoom_requests)), (3, 0))
        self.assertNotIn("zoom", out.getvalue())
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
