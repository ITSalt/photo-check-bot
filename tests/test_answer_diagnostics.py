"""WP-BOT-15: fixed rule codes for format rejects; model text never reaches a log or controls output."""
import ast
import contextlib
import copy
import functools
import inspect
import io
import json
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from app import answer_rules, vision_compare, vision_controls, vision_core
from app.vision_request import AnswerError, MAX_ATTRIBUTES
from core_t2 import photo_compare_t2_candidate as t2
from tests.test_t4_vision import ENV, INV, JPEG, PNG, FakeModel, chat, compare_answer

MARK = "ZQMARKERQZ"
VALIDATED = vision_core.parse_inventory(chat({"attributes": INV}))
SHA = VALIDATED["sha256"]


def inventory_reject(answer):
    with unittest.TestCase().assertRaises(AnswerError) as ctx:
        vision_core.parse_inventory(chat(answer))
    return ctx.exception


def compare_reject(mutate):
    answer = compare_answer(SHA)
    mutate(answer)
    with unittest.TestCase().assertRaises(AnswerError) as ctx:
        vision_core.parse_compare(chat(answer), VALIDATED)
    return ctx.exception


def wrap(mutator):
    a = {"attributes": copy.deepcopy(INV)}
    mutator(a["attributes"])
    return a


class InventoryRules(unittest.TestCase):
    def check(self, answer, rule, path=None, **detail):
        exc = inventory_reject(answer)
        self.assertEqual(exc.args, ("BAD_INVENTORY",))
        self.assertEqual((exc.rule, exc.path, exc.detail), (rule, path, detail))

    def test_each_inventory_rule(self):
        self.check({"attributes": copy.deepcopy(INV), "extra": 1}, "WRAPPER_KEYS")
        self.check({}, "WRAPPER_KEYS")
        self.check({"attributes": "x"}, "NO_ATTRIBUTES", "attributes")
        self.check({"attributes": []}, "NO_ATTRIBUTES", "attributes")
        many = [dict(INV[0], attribute_id="a%d" % i) for i in range(MAX_ATTRIBUTES + 1)]
        self.check({"attributes": many}, "TOO_MANY_ATTRIBUTES", "attributes")
        self.check({"attributes": ["x"]}, "NOT_OBJECT", "attributes[0]")
        self.check(wrap(lambda a: a[1].pop("claim")), "FIELDS_MISSING", "attributes[1]", missing="claim")
        self.check(wrap(lambda a: a[0].update(zzz=1)), "FIELDS_UNKNOWN", "attributes[0]", count=1)
        self.check(wrap(lambda a: a[0].update(claim="")), "TEXT_LENGTH", "attributes[0].claim")
        self.check(wrap(lambda a: a[1].update(attribute_id=5)), "TEXT_LENGTH", "attributes[1].attribute_id")
        self.check(wrap(lambda a: a[1].update(reference_roi=[1, 2, 3])), "ROI_SHAPE",
                   "attributes[1].reference_roi")
        self.check(wrap(lambda a: a[0].update(reference_roi=[1000, 900, 300, 300])), "ROI_OUT_OF_FRAME",
                   "attributes[0].reference_roi")
        self.check(wrap(lambda a: a[1].update(attribute_id=a[0]["attribute_id"])), "DUP_ATTR",
                   "attributes[1].attribute_id")

    def test_unknown_field_name_is_never_kept(self):
        exc = inventory_reject(wrap(lambda a: a[0].update({MARK: 1, MARK + "2": 2})))
        self.assertEqual((exc.rule, exc.detail), ("FIELDS_UNKNOWN", {"count": 2}))
        self.assertNotIn(MARK, repr(vars(exc)))


class CompareRules(unittest.TestCase):
    def check(self, mutate, rule, path=None, **detail):
        exc = compare_reject(mutate)
        self.assertEqual(exc.args, ("BAD_COMPARE",))
        self.assertEqual((exc.rule, exc.path, exc.detail), (rule, path, detail))

    def test_each_compare_rule(self):
        def res(a, i=0):
            return a["results"][i]
        self.check(lambda a: a.update(extra=1), "FIELDS_UNKNOWN", None, count=1)
        self.check(lambda a: a.pop("unmapped_candidate_elements"), "FIELDS_MISSING", None,
                   missing="unmapped_candidate_elements")
        self.check(lambda a: a.update(schema_version="photo-compare/1"), "SCHEMA_VERSION", "schema_version")
        self.check(lambda a: a.update(inventory_sha256="XYZ"), "SHA_FORMAT", "inventory_sha256")
        self.check(lambda a: a.update(inventory_sha256="0" * 64), "SHA_MISMATCH", "inventory_sha256")
        self.check(lambda a: a.update(results="x"), "RESULTS_NOT_LIST")
        self.check(lambda a: a.update(unmapped_candidate_elements={}), "RESULTS_NOT_LIST")
        self.check(lambda a: res(a, 1).update(attribute_id="nope"), "ATTR_UNKNOWN_OR_DUP",
                   "results[1].attribute_id")
        self.check(lambda a: res(a, 1).update(attribute_id=res(a)["attribute_id"]), "ATTR_UNKNOWN_OR_DUP",
                   "results[1].attribute_id")
        self.check(lambda a: res(a).update(verdict="MAYBE"), "VERDICT_EVIDENCE", "results[0]")
        self.check(lambda a: res(a).update(evidence_quality="NONE"), "VERDICT_EVIDENCE", "results[0]")
        self.check(lambda a: res(a).update(verdict="NOT_VISIBLE_IN_REFERENCE", evidence_quality="PARTIAL",
                                           reference_roi=[0, 0, 100, 100]),
                   "ROI_NOT_NULL", "results[0].reference_roi")
        self.check(lambda a: a["results"].pop(), "RESULTS_MISSING", "results")
        self.check(lambda a: res(a).update(observation="x" * 161), "TEXT_LENGTH", "results[0].observation")
        self.check(lambda a: res(a).update(candidate_roi=[1, 2, 3, "4"]), "ROI_SHAPE",
                   "results[0].candidate_roi")
        self.check(lambda a: res(a).update(reference_roi=[0, 0, 5, 5]), "ROI_OUT_OF_FRAME",
                   "results[0].reference_roi")
        self.check(lambda a: res(a).pop("observation"), "FIELDS_MISSING", "results[0]", missing="observation")
        self.check(lambda a: res(a).update(zz=1), "FIELDS_UNKNOWN", "results[0]", count=1)
        self.check(lambda a: a["results"].__setitem__(0, 7), "NOT_OBJECT", "results[0]")

        def unm(**kw):
            return dict({"candidate_roi": [10, 10, 100, 100], "description": "d",
                         "visibility_in_reference": "VISIBLE_AND_ABSENT"}, **kw)
        self.check(lambda a: a.update(unmapped_candidate_elements=[unm(visibility_in_reference="?")]),
                   "VISIBILITY_VALUE", "unmapped_candidate_elements[0].visibility_in_reference")
        self.check(lambda a: a.update(unmapped_candidate_elements=[unm(description="x" * 121)]),
                   "TEXT_LENGTH", "unmapped_candidate_elements[0].description")
        self.check(lambda a: a.update(unmapped_candidate_elements=[unm(candidate_roi=[1000, 0, 5, 5])]),
                   "ROI_OUT_OF_FRAME", "unmapped_candidate_elements[0].candidate_roi")


# One trigger per ContractError raise site of validate_inventory / validate_response and the
# helpers they call. Key: "<function>:<site>"; value: (callable that raises, expected rule).
def _inv(**edit):
    base = {"schema_version": "photo-inventory/1", "reference_photo_id": "reference",
            "attributes": copy.deepcopy(INV)}
    base.update(edit)
    return base


def _vi(inv, **kw):
    return lambda: t2.validate_inventory(inv, expected_reference_photo_id="reference", **kw)


def _vr(mutate, ids=None, sha=SHA):
    def run():
        a = compare_answer(SHA)
        mutate(a)
        t2.validate_response(a, ids if ids is not None else VALIDATED["attribute_ids"],
                             expected_inventory_sha256=sha)
    return run


def _bad_inv_attr(fn):
    inv = _inv()
    fn(inv["attributes"][0])
    return inv


SITES = {
    "canonical_json": (lambda: t2.canonical_json({"a": float("nan")}), "NOT_CANONICAL"),
    "_fields:not_object": (_vi(_inv(attributes=[5])), "NOT_OBJECT"),
    "_fields:keys": (_vi(_inv(extra=1)), "FIELDS_UNKNOWN"),
    "_text": (_vi(_bad_inv_attr(lambda a: a.update(claim=" "))), "TEXT_LENGTH"),
    "_hex64": (_vi(_inv(), expected_sha256="nothex"), "SHA_FORMAT"),
    "validate_roi:shape": (_vi(_bad_inv_attr(lambda a: a.update(reference_roi="x"))), "ROI_SHAPE"),
    "validate_roi:frame": (_vi(_bad_inv_attr(lambda a: a.update(reference_roi=[0, 0, 1, 1]))), "ROI_OUT_OF_FRAME"),
    "validate_inventory:schema": (_vi(_inv(schema_version="x")), "SCHEMA_VERSION"),
    "validate_inventory:ref": (_vi(_inv(reference_photo_id="other")), "REF_ID"),
    "validate_inventory:empty": (_vi(_inv(attributes=[])), "NO_ATTRIBUTES"),
    "validate_inventory:dup": (_vi(_inv(attributes=[INV[0], INV[0]])), "DUP_ATTR"),
    "validate_inventory:pinned": (_vi(_inv(), expected_sha256="0" * 64), "SHA_MISMATCH"),
    "validate_response:ids": (_vr(lambda a: None, ids=[]), "EXPECTED_IDS"),
    "validate_response:schema": (_vr(lambda a: a.update(schema_version="x")), "SCHEMA_VERSION"),
    "validate_response:sha": (_vr(lambda a: a.update(inventory_sha256="1" * 64)), "SHA_MISMATCH"),
    "validate_response:lists": (_vr(lambda a: a.update(results=None)), "RESULTS_NOT_LIST"),
    "validate_response:attr": (_vr(lambda a: a["results"][0].update(attribute_id="q")), "ATTR_UNKNOWN_OR_DUP"),
    "validate_response:verdict": (_vr(lambda a: a["results"][0].update(verdict="q")), "VERDICT_EVIDENCE"),
    "validate_response:roi_null": (_vr(lambda a: a["results"][0].update(
        verdict="NOT_VISIBLE_IN_REFERENCE", evidence_quality="PARTIAL")), "ROI_NOT_NULL"),
    "validate_response:missing": (_vr(lambda a: a["results"].pop()), "RESULTS_MISSING"),
    "validate_response:visibility": (_vr(lambda a: a["unmapped_candidate_elements"].append(
        {"candidate_roi": [0, 0, 100, 100], "description": "d", "visibility_in_reference": "q"})),
        "VISIBILITY_VALUE"),
}
SITE_FUNCTIONS = ("canonical_json", "_fields", "_text", "_hex64", "validate_roi",
                  "validate_inventory", "validate_response")


def raise_sites():
    tree = ast.parse(inspect.getsource(t2))
    return sum(1 for fn in tree.body if isinstance(fn, ast.FunctionDef) and fn.name in SITE_FUNCTIONS
               for node in ast.walk(fn) if isinstance(node, ast.Raise)
               and isinstance(node.exc, ast.Call) and getattr(node.exc.func, "id", "") == "ContractError")


class ContractErrorSites(unittest.TestCase):
    def test_every_raise_site_maps_to_its_rule_and_never_other(self):
        for site, (trigger, rule) in SITES.items():
            with self.subTest(site=site):
                with self.assertRaises(t2.ContractError) as ctx:
                    trigger()
                got = answer_rules.classify(str(ctx.exception))[0]
                self.assertEqual(got, rule)
                self.assertNotEqual(got, "OTHER")

    def test_site_table_covers_every_raise(self):
        self.assertEqual(len(SITES), raise_sites())

    def test_rule_table_is_closed(self):
        for _, rule in SITES.values():
            self.assertIn(rule, answer_rules.RULES)
        self.assertEqual(answer_rules.classify("something new: happened")[0], "OTHER")
        self.assertEqual(answer_rules.fixed("MADE_UP", "a b")[:2], ("OTHER", None))


def attacks():
    """(kind, inventory answer, compare mutation); MARK sits in every free-text and value position."""
    def inv(f):
        return ("inv", wrap(f), None)

    def cmp(f):
        return ("cmp", None, f)

    def r0(a):
        return a["results"][0]
    unm = {"candidate_roi": [10, 10, 100, 100], "description": MARK, "visibility_in_reference": "VISIBLE_AND_ABSENT"}
    return [
        inv(lambda a: a[0].update(claim=MARK, reference_roi=[MARK, 1, 1, 1])),
        inv(lambda a: a[0].update(claim=MARK, reference_roi=[MARK] * 4)),
        inv(lambda a: a[0].update({MARK: MARK})),
        inv(lambda a: a[1].update(attribute_id=a[0]["attribute_id"], claim=MARK)),
        inv(lambda a: a[0].update(attribute_id=MARK * 3, claim=MARK, reference_roi=[999, 999, 99, 99])),
        inv(lambda a: a[0].update(attribute_id=MARK, claim=MARK, extra=MARK)),
        inv(lambda a: a.append({MARK: MARK})),
        ("inv", {"attributes": [dict(INV[0], claim=MARK)], MARK: MARK}, None),
        ("inv", {"attributes": MARK}, None),
        ("inv", {MARK: [MARK]}, None),
        cmp(lambda a: r0(a).update(attribute_id=MARK)),
        cmp(lambda a: r0(a).update(verdict=MARK, observation=MARK)),
        cmp(lambda a: r0(a).update(evidence_quality=MARK)),
        cmp(lambda a: r0(a).update(reference_roi=[MARK, MARK, MARK, MARK], observation=MARK)),
        cmp(lambda a: r0(a).update(candidate_roi=[MARK])),
        cmp(lambda a: r0(a).update(candidate_roi=[0, 0, 5, 5], observation=MARK)),
        cmp(lambda a: r0(a).update({MARK: MARK})),
        cmp(lambda a: a.update({MARK: MARK})),
        cmp(lambda a: a.update(schema_version=MARK)),
        cmp(lambda a: a.update(inventory_sha256=MARK)),
        cmp(lambda a: r0(a).update(observation=MARK * 100)),
        cmp(lambda a: a.update(unmapped_candidate_elements=[dict(unm, description=MARK * 100)])),
        cmp(lambda a: a.update(unmapped_candidate_elements=[dict(unm, visibility_in_reference=MARK)])),
        cmp(lambda a: a.update(unmapped_candidate_elements=[dict(unm, candidate_roi=[MARK], **{MARK: 1})])),
        cmp(lambda a: a.update(results=MARK)),
        cmp(lambda a: a.update(results=[MARK])),
        cmp(lambda a: r0(a).update(verdict="NOT_VISIBLE_IN_REFERENCE", evidence_quality="PARTIAL",
                                   reference_roi=[MARK])),
    ]


class AttackModel(FakeModel):
    """Plays the model with one attack: an inventory attack replaces call 0, a compare attack mutates the answers."""
    def __init__(self, kind, inv_answer, mutate):
        super().__init__(mutate=(lambda i, a: mutate(a)) if (kind == "cmp" and mutate) else None)
        self.kind, self.inv_answer = kind, inv_answer

    def __call__(self, request, headers, timeout):
        if sum(p["type"] == "image_url" for p in request["messages"][0]["content"]) == 1:
            self.requests = []   # a new check starts: FakeModel indexes calls per check
        if self.kind == "inv" and not self.requests:
            self.requests.append(request)
            return {"status": 200, "body": chat(self.inv_answer)}
        return super().__call__(request, headers, timeout)


class CaptureLogs:
    """Every log record of every logger, as text."""
    def __enter__(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setLevel(logging.DEBUG)
        self.root = logging.getLogger()
        self.old = self.root.level
        self.root.setLevel(logging.DEBUG)
        self.root.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        self.root.removeHandler(self.handler)
        self.root.setLevel(self.old)

    @property
    def text(self):
        return self.stream.getvalue()


class Privacy(unittest.TestCase):
    def test_marker_never_reaches_a_log_line(self):
        for n, (kind, inv_answer, mutate) in enumerate(attacks()):
            with self.subTest(attack=n):
                model = AttackModel(kind, inv_answer, mutate)
                compare = vision_compare.compare_from_env(ENV, transport=model)
                with CaptureLogs() as logs:
                    result = compare(JPEG, PNG)
                self.assertEqual(result, {"status": "UNAVAILABLE"})
                self.assertIn("vision_bad_answer", logs.text)   # the attack is a format reject
                self.assertIn(" rule=", logs.text)
                self.assertNotIn(MARK, logs.text)
                self.assertNotIn("ZQ", logs.text)

    def test_marker_never_reaches_controls_output(self):
        rows = [{"kind": "same", "ref": "a"}, {"kind": "different", "ref": "a", "cand": "b"}]
        with tempfile.TemporaryDirectory() as d:
            manifest = os.path.join(d, "m.json")
            with open(manifest, "w") as f:
                json.dump(rows, f)
            for n, (kind, inv_answer, mutate) in enumerate(attacks()):
                model = AttackModel(kind, inv_answer, mutate)
                fake = functools.partial(vision_compare.compare_from_env, transport=model)
                with self.subTest(attack=n), patch.object(vision_compare, "compare_from_env", fake):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), CaptureLogs() as logs:
                        vision_controls._main([manifest, "--repeats", "1"], ENV, read=lambda p: JPEG)
                    self.assertNotIn(MARK, out.getvalue())
                    self.assertNotIn(MARK, logs.text)
                    self.assertIn("rejects by stage x rule:\n  stage=", out.getvalue())
                    self.assertNotIn(d, out.getvalue())
                    self.assertNotIn("path=", out.getvalue())


class LogLineAndControls(unittest.TestCase):
    def test_log_line_format(self):
        model = FakeModel(mutate=lambda i, a: a["results"][1].update(verdict="MAYBE"))
        compare = vision_compare.compare_from_env(ENV, transport=model)
        with self.assertLogs("vision", "WARNING") as logs:
            self.assertEqual(compare(JPEG, PNG), {"status": "UNAVAILABLE"})
        self.assertEqual(logs.output, ["WARNING:vision:vision_bad_answer stage=compare_a code=BAD_COMPARE "
                                       "rule=VERDICT_EVIDENCE path=results[1]"])

    def test_inventory_stage_line(self):
        model = FakeModel(inventory=INV + [{"attribute_id": "z", "claim": "c"}])
        compare = vision_compare.compare_from_env(ENV, transport=model)
        with self.assertLogs("vision", "WARNING") as logs:
            compare(JPEG, PNG)
        self.assertEqual(logs.output, ["WARNING:vision:vision_bad_answer stage=inventory code=BAD_INVENTORY "
                                       "rule=FIELDS_MISSING path=attributes[%d] missing=reference_roi" % len(INV)])

    def test_answer_errors_without_a_rule_keep_the_old_line(self):
        model = FakeModel(fail_at=0, reply={"status": 200, "body": chat("not json")})
        compare = vision_compare.compare_from_env(ENV, transport=model)
        with self.assertLogs("vision", "WARNING") as logs:
            compare(JPEG, PNG)
        self.assertEqual(logs.output, ["WARNING:vision:vision_bad_answer stage=inventory code=NOT_JSON"])

    def test_controls_table_counts_by_stage_and_rule(self):
        rows = [{"kind": "same", "ref": "a"}, {"kind": "different", "ref": "a", "cand": "b"}]
        with tempfile.TemporaryDirectory() as d:
            manifest = os.path.join(d, "m.json")
            with open(manifest, "w") as f:
                json.dump(rows, f)
            model = AttackModel("cmp", None, lambda a: a["results"].pop())
            fake = functools.partial(vision_compare.compare_from_env, transport=model)
            out = io.StringIO()
            with patch.object(vision_compare, "compare_from_env", fake), \
                    contextlib.redirect_stdout(out), CaptureLogs():
                vision_controls._main([manifest, "--repeats", "2"], ENV, read=lambda p: JPEG)
            self.assertIn("rejects by stage x rule:\n  stage=compare_a rule=RESULTS_MISSING n=4", out.getvalue())

    def test_empty_table_says_none(self):
        self.assertEqual(answer_rules.RejectTally().lines(), ["rejects by stage x rule: none"])

    def test_tally_refuses_odd_stage_and_rule(self):
        t = answer_rules.RejectTally()
        t.add("stage with " + MARK, "BAD_COMPARE", MARK)
        self.assertEqual(t.counts, {("unknown", "OTHER"): 1})


if __name__ == "__main__":
    unittest.main()
