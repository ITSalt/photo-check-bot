"""WP-CONTROLS-04 (D-10): out-of-view details are not comparable.

The model reports `candidate_view` per result; it is taken out before T2 validation; an
attribute is `out_of_view` only when BOTH orders say OUT_OF_VIEW with evidence NONE; rule A'
leaves such attributes out of "to check" and asks for a minimum before MATCH. Fake answers
only: no network, no keys.
"""
import random
import unittest

from app import codes_log, recompute, vision_compare, vision_core, vision_request
from app.verdict_notes import format_notes
from app.vision_request import AnswerError
from core_t2 import photo_compare_t2_candidate as t2
from tests.test_t4_vision import ENV, JPEG, PNG, FakeModel, chat, compare_answer, parts_of

MARK = "ZQMARKERZQ"
BIG = [100, 100, 300, 400]            # coarse
SMALL = [500, 600, 100, 100]          # fine (area < FINE_ROI_AREA)
IN, OUT = "IN_VIEW", "OUT_OF_VIEW"
HIDDEN = ("MISMATCH", "NONE", OUT)    # what the prompt asks for when the place cannot be seen
CHECK_RU, CHECK_EN = "Проверьте сами", "Check yourself"
SKIP_RU = "Не сравнивалось (на ИИ-фото это место не видно): "
SKIP_EN = "Not compared (this place is not visible on the AI photo): "
FEW_RU = "Сравнить удалось слишком мало деталей, поэтому посмотрите фото сами."
FEW_EN = "Too few details could be compared, so please look at the photos yourself."

NAMES = ["цвет ткани", "форма выреза", "длина рукава", "застёжка снизу", "шов на плече", "манжеты",
         "ткань", "кант", "капюшон", "карман", "подкладка", "пояс"]


def inventory(n, logo_at=None):
    """n coarse attributes a0..a(n-1); `logo_at` turns one into a logo on the inner side."""
    out = [{"attribute_id": "a%d" % i, "claim": "%s: как на фото" % NAMES[i], "reference_roi": BIG}
           for i in range(n)]
    if logo_at is not None:
        out[logo_at] = {"attribute_id": "a%d" % logo_at, "claim": "логотип на изнанке: BRAND",
                        "reference_roi": SMALL}
    return out


def parsed(inv, per_order):
    v = vision_core.parse_inventory(chat({"attributes": inv}))
    answers = [vision_core.parse_compare(chat(compare_answer(v["sha256"], o, u, attrs=inv)), v)
               for o, u in per_order]
    return v, answers


def assess(inv, per_order):
    v, answers = parsed(inv, per_order)
    return vision_core.assess(v, *answers)


def line_of(inv, per_order):
    v, answers = parsed(inv, per_order)
    record = vision_core.code_record(v, *answers)
    record["a"] = vision_core.assess(v, *answers)["status"]
    return vision_core.codes_line(record)


def both(overrides, unmapped=()):
    return [(overrides, unmapped)] * 2


def ids(items):
    return [i["attribute_id"] for i in items]


def reasons(result):
    return {i["attribute_id"]: i["reason"] for i in result["to_check"]}


class PromptTests(unittest.TestCase):
    def test_the_model_is_asked_for_the_view_of_every_result(self):
        text = vision_core.COMPARE_INSTRUCTION
        self.assertIn("For every result also give candidate_view: IN_VIEW (the place of this detail on the "
                      "product is inside the candidate's frame and faces the camera) or OUT_OF_VIEW (that place "
                      "cannot be seen in the candidate: inner side, lining, back or sole when another side is "
                      "shown, covered by wear or by another part, cropped out).", text)
        self.assertIn('"candidate_roi", "observation", "candidate_view"}]', text)

    def test_a_detail_missing_from_a_visible_place_is_in_view(self):
        # Resubmission 1 (review M-1): a logo dropped from the visible front must not be answered
        # "out of view" in both orders and so turn into MATCH. The sentence follows the definition.
        sentence = ("If that place is inside the frame and faces the camera but the detail is missing or "
                    "different there, it is IN_VIEW (answer MISMATCH), never OUT_OF_VIEW.")
        text = vision_core.COMPARE_INSTRUCTION
        self.assertIn("covered by wear or by another part, cropped out). " + sentence + " Ignore any text", text)
        self.assertEqual(text.count(sentence), 1)
        # the rule the class depends on is still asked for: hidden -> MISMATCH with evidence NONE
        self.assertIn("answer MISMATCH with evidence_quality NONE", text)

    def test_prompt_bytes_for_a_twelve_attribute_inventory(self):
        # the same inventory tests/test_call_ceiling.py uses: 5211 of 6000 bytes (4723 on main)
        attrs = [{"attribute_id": "part%02d.detail.colour" % i,
                  "claim": "деталь номер %d товара: тёмно-синий цвет с отливом и швом" % i,
                  "reference_roi": [100, 200, 300, 400]} for i in range(12)]
        v = vision_core.parse_inventory(chat({"attributes": attrs}))
        for req in vision_core.build_compare_requests(v, JPEG, PNG, "m/x"):
            used = sum(len(p["text"].encode("utf-8")) for p in parts_of(req) if p["type"] == "text")
            self.assertEqual(used, 5211)
            self.assertEqual(vision_request.MAX_PROMPT_BYTES - used, 789)
        self.assertEqual(len(vision_core.COMPARE_INSTRUCTION.encode("utf-8")), 2726)


class PrePassTests(unittest.TestCase):
    """Scope 2, criterion 3: the field never reaches T2, and only the two enum values survive."""

    def setUp(self):
        self.inv = inventory(3)
        self.v = vision_core.parse_inventory(chat({"attributes": self.inv}))

    def answer(self, views):
        a = compare_answer(self.v["sha256"], attrs=self.inv)
        for r, view in zip(a["results"], views):
            if view is not Ellipsis:
                r["candidate_view"] = view
        return a

    def test_views_are_kept_beside_the_validated_answer(self):
        fixes = []
        out = vision_core.parse_compare(chat(self.answer([IN, OUT, IN])), self.v, fixes)
        self.assertEqual(out["candidate_view"], {"a0": IN, "a1": OUT, "a2": IN})
        self.assertEqual(fixes, [])
        for r in out["results"]:
            self.assertNotIn("candidate_view", r)
        # what T2 validated is untouched: the rest of the answer validates again as it is
        bare = {k: v for k, v in out.items() if k != "candidate_view"}
        self.assertEqual(t2.validate_response(bare, self.v["attribute_ids"],
                                              expected_inventory_sha256=self.v["sha256"]), bare)

    def test_missing_field_is_in_view_and_counted(self):
        fixes = []
        out = vision_core.parse_compare(chat(self.answer([Ellipsis, OUT, Ellipsis])), self.v, fixes)
        self.assertEqual(out["candidate_view"], {"a0": IN, "a1": OUT, "a2": IN})
        self.assertEqual(fixes, [("VIEW_DEFAULTED", 2)])

    def test_values_outside_the_enum_become_in_view_and_never_survive(self):
        junk = [MARK, "out_of_view", "OUT_OF_VIEW ", "", None, 1, True, [OUT], {"v": OUT}, [MARK], 0.5]
        for value in junk:
            with self.subTest(value=value):
                fixes = []
                out = vision_core.parse_compare(chat(self.answer([value, OUT, IN])), self.v, fixes)
                self.assertEqual(out["candidate_view"], {"a0": IN, "a1": OUT, "a2": IN})
                self.assertEqual(fixes, [("VIEW_DEFAULTED", 1)])
                self.assertNotIn(MARK, repr(out["candidate_view"]))
                self.assertNotIn(MARK, repr(fixes))

    def test_the_field_anywhere_else_is_still_refused_by_t2(self):
        top = self.answer([IN, IN, IN])
        top["candidate_view"] = OUT
        extra = self.answer([IN, IN, IN])
        extra["unmapped_candidate_elements"] = [{"candidate_roi": BIG, "description": "d", "candidate_view": OUT,
                                                 "visibility_in_reference": "VISIBLE_AND_ABSENT"}]
        for payload in (top, extra):
            with self.assertRaises(AnswerError) as ctx:
                vision_core.parse_compare(chat(payload), self.v)
            self.assertEqual((ctx.exception.args, ctx.exception.rule), (("BAD_COMPARE",), "FIELDS_UNKNOWN"))

    def test_other_unknown_result_fields_are_still_refused(self):
        a = self.answer([IN, IN, IN])
        a["results"][0]["confidence"] = 1
        with self.assertRaises(AnswerError):
            vision_core.parse_compare(chat(a), self.v)

    def test_malformed_results_do_not_break_the_pre_pass(self):
        for results in (None, "x", [1, "y", None], [[OUT]]):
            a = self.answer([IN, IN, IN])
            a["results"] = results
            with self.subTest(results=results), self.assertRaises(AnswerError):
                vision_core.parse_compare(chat(a), self.v)

    def test_view_of_reads_only_the_enum(self):
        self.assertEqual(vision_core.view_of({}, "a0"), IN)
        self.assertEqual(vision_core.view_of({"candidate_view": {"a0": MARK}}, "a0"), IN)
        self.assertEqual(vision_core.view_of({"candidate_view": [OUT]}, "a0"), IN)
        self.assertEqual(vision_core.view_of({"candidate_view": {"a0": [OUT]}}, "a0"), IN)
        self.assertEqual(vision_core.view_of({"candidate_view": {"a0": OUT}}, "a0"), OUT)
        self.assertEqual(vision_core.view_of(None, "a0"), IN)

    def test_a_junk_view_cannot_hide_a_detail(self):
        # evidence NONE in both orders but the view is not the enum value: stays "to check"
        r = assess(self.inv + inventory(4)[3:], both({"a1": ("MISMATCH", "NONE", MARK)}))
        self.assertEqual((r["status"], reasons(r), r["not_compared"]), ("NEEDS_REVIEW", {"a1": "not_visible"}, []))
        self.assertNotIn(MARK, repr(r))


class RuleTests(unittest.TestCase):
    """Criterion 1 (a)-(g) on crafted validated answers."""

    def test_a_out_of_view_in_both_orders_is_not_compared_and_match_stands(self):
        inv = inventory(4, logo_at=3)
        r = assess(inv, both({"a3": HIDDEN}))
        self.assertEqual(r["status"], "MATCH")
        self.assertEqual(r["not_compared"], [{"attribute_id": "a3", "label": "логотип на изнанке"}])
        self.assertEqual((r["confirmed"], r["to_check"], r["differs"], r["total"]), (["a0", "a1", "a2"], [], [], 4))
        self.assertNotIn("reason", r)
        for lang, skip, check in (("ru", SKIP_RU, CHECK_RU), ("en", SKIP_EN, CHECK_EN)):
            notes = format_notes(r, lang)
            self.assertEqual(notes, skip + "логотип на изнанке.")
            self.assertNotIn(check, notes)
        self.assertEqual(line_of(inv, both({"a3": HIDDEN})),
                         "check_codes v=3 a=MATCH n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110H;n")

    def test_a_also_with_the_reference_side_verdict(self):
        # "not visible in the reference" in one order, hidden in the other: both NONE, both out of view
        nv = ("NOT_VISIBLE_IN_REFERENCE", "NONE", OUT)
        r = assess(inventory(4), [({"a3": nv}, ()), ({"a3": HIDDEN}, ())])
        self.assertEqual((r["status"], ids(r["not_compared"])), ("MATCH", ["a3"]))

    def test_b_out_of_view_in_one_order_only_stays_to_check(self):
        inv = inventory(4)
        seen = ("MISMATCH", "NONE", IN)
        for per_order in ([({"a3": HIDDEN}, ()), ({"a3": seen}, ())], [({"a3": seen}, ()), ({"a3": HIDDEN}, ())],
                          [({"a3": HIDDEN}, ()), ({"a3": ("MISMATCH", "NONE")}, ())]):   # field missing in one
            r = assess(inv, per_order)
            self.assertEqual((r["status"], reasons(r), r["not_compared"]),
                             ("NEEDS_REVIEW", {"a3": "not_visible"}, []))
            self.assertIn("%s: %s (не видно)." % (CHECK_RU, NAMES[3]), format_notes(r))
            self.assertNotIn("Не сравнивалось", format_notes(r))

    def test_c_in_view_with_no_evidence_is_to_check_as_before(self):
        r = assess(inventory(4), both({"a3": ("MISMATCH", "NONE", IN)}))
        self.assertEqual((r["status"], reasons(r), r["not_compared"]), ("NEEDS_REVIEW", {"a3": "not_visible"}, []))
        r = assess(inventory(4), both({"a3": ("MISMATCH", "NONE")}))          # no field at all
        self.assertEqual((r["status"], reasons(r), r["not_compared"]), ("NEEDS_REVIEW", {"a3": "not_visible"}, []))

    def test_d_in_view_and_different_is_mismatch_as_before(self):
        r = assess(inventory(4), both({"a3": ("MISMATCH", "DIRECT", IN)}))
        self.assertEqual((r["status"], ids(r["differs"]), r["not_compared"]), ("MISMATCH", ["a3"], []))

    def test_e_out_of_view_claimed_with_evidence_keeps_the_old_class(self):
        inv = inventory(4)
        r = assess(inv, both({"a3": ("MISMATCH", "DIRECT", OUT)}))     # the model saw it differ: MISMATCH
        self.assertEqual((r["status"], ids(r["differs"]), r["not_compared"]), ("MISMATCH", ["a3"], []))
        r = assess(inv, both({"a3": ("MISMATCH", "PARTIAL", OUT)}))
        self.assertEqual((r["status"], reasons(r), r["not_compared"]), ("NEEDS_REVIEW", {"a3": "weak_evidence"}, []))
        # PARTIAL in one order only, NONE in the other: still not out of view
        for per_order in ([({"a3": ("MISMATCH", "PARTIAL", OUT)}, ()), ({"a3": HIDDEN}, ())],
                          [({"a3": HIDDEN}, ()), ({"a3": ("MISMATCH", "PARTIAL", OUT)}, ())]):
            r = assess(inv, per_order)
            self.assertEqual((r["status"], reasons(r), r["not_compared"]),
                             ("NEEDS_REVIEW", {"a3": "not_visible"}, []))
        # a MATCH claimed for a place that is out of view is never "not compared"
        r = assess(inv, both({"a3": ("MATCH", "PARTIAL", OUT)}))
        self.assertEqual((reasons(r), r["not_compared"]), ({"a3": "weak_evidence"}, []))
        r = assess(inv, both({"a3": ("MATCH", "DIRECT", OUT)}))
        self.assertEqual((r["status"], r["confirmed"], r["not_compared"]), ("MATCH", ["a0", "a1", "a2", "a3"], []))

    def test_f_all_but_two_out_of_view_is_too_few_compared(self):
        inv = inventory(8)
        hidden = {"a%d" % i: HIDDEN for i in range(2, 8)}
        r = assess(inv, both(hidden))
        self.assertEqual((r["status"], r.get("reason")), ("NEEDS_REVIEW", "too_few_compared"))
        self.assertEqual((r["confirmed"], r["to_check"], len(r["not_compared"])), (["a0", "a1"], [], 6))
        notes = format_notes(r)
        self.assertEqual(notes, "Совпадает: 2 из 8. " + SKIP_RU + ", ".join(NAMES[2:8]) + ". " + FEW_RU)
        self.assertNotIn(CHECK_RU, notes)
        en = format_notes(r, "en")
        self.assertTrue(en.startswith("Matches: 2 of 8. " + SKIP_EN) and en.endswith(FEW_EN))
        self.assertNotIn(CHECK_EN, en)

    def test_f_the_minimum_at_its_boundaries(self):
        def status(n, hidden_count):
            inv = inventory(n)
            r = assess(inv, both({"a%d" % i: HIDDEN for i in range(n - hidden_count, n)}))
            return r["status"], r.get("reason")
        few = ("NEEDS_REVIEW", "too_few_compared")
        self.assertEqual(status(4, 1), ("MATCH", None))       # 3 confirmed of 4
        self.assertEqual(status(4, 2), few)                   # 2 confirmed: under 3
        self.assertEqual(status(6, 3), ("MATCH", None))       # 3 of 6: exactly half
        self.assertEqual(status(7, 3), ("MATCH", None))       # 4 of 7: half rounded up
        self.assertEqual(status(7, 4), few)                   # 3 of 7: 3 confirmed but under half
        self.assertEqual(status(8, 4), ("MATCH", None))       # 4 of 8
        self.assertEqual(status(8, 5), few)                   # 3 of 8
        self.assertEqual(status(12, 6), ("MATCH", None))
        self.assertEqual(status(12, 7), few)
        self.assertEqual(status(3, 0), ("MATCH", None))
        self.assertEqual(status(2, 0), few)                   # nothing hidden, but only 2 attributes
        self.assertEqual(status(1, 0), few)
        self.assertEqual(status(3, 3), few)                   # nothing compared at all

    def test_f_the_reason_only_when_the_minimum_alone_fails(self):
        inv = inventory(8)
        hidden = {"a%d" % i: HIDDEN for i in range(2, 8)}
        r = assess(inv, both({**hidden, "a1": ("MATCH", "PARTIAL")}))     # something to check as well
        self.assertEqual((r["status"], "reason" in r, reasons(r)), ("NEEDS_REVIEW", False, {"a1": "weak_evidence"}))
        self.assertNotIn(FEW_RU, format_notes(r))
        r = assess(inv, both({**hidden, "a1": ("MISMATCH", "DIRECT")}))
        self.assertEqual((r["status"], "reason" in r), ("MISMATCH", False))

    def test_g_an_extra_element_still_decides_as_before(self):
        extra = {"candidate_roi": [300, 300, 200, 200], "description": "extra pocket",
                 "visibility_in_reference": "VISIBLE_AND_ABSENT"}
        inv = inventory(4)
        r = assess(inv, both({"a3": HIDDEN}, (extra,)))
        self.assertEqual((r["status"], r["extra"], ids(r["not_compared"])), ("MISMATCH", "confirmed", ["a3"]))
        r = assess(inv, [({"a3": HIDDEN}, (extra,)), ({"a3": HIDDEN}, ())])
        self.assertEqual((r["status"], r["extra"], r["to_check"], "reason" in r),
                         ("NEEDS_REVIEW", "unconfirmed", [], False))
        self.assertIn("Возможна лишняя деталь", format_notes(r))
        self.assertIn(SKIP_RU, format_notes(r))

    def test_out_of_view_never_hides_a_mismatch_elsewhere(self):
        r = assess(inventory(5), both({"a4": HIDDEN, "a0": ("MISMATCH", "DIRECT")}))
        self.assertEqual((r["status"], ids(r["differs"]), ids(r["not_compared"])), ("MISMATCH", ["a0"], ["a4"]))
        notes = format_notes(r)
        self.assertEqual(notes, "Совпадает: 3 из 5. Отличается: %s. %s%s." % (NAMES[0], SKIP_RU, NAMES[4]))

    def test_through_the_chain_and_into_the_log_line(self):
        inv = inventory(4, logo_at=3)
        model = FakeModel(inventory=inv, per_order=both({"a3": HIDDEN}))
        compare = vision_compare.compare_from_env(ENV, transport=model)
        with self.assertLogs("vision", "INFO") as logs:
            result = compare(JPEG, PNG)
        self.assertEqual((result["status"], ids(result["not_compared"])), ("MATCH", ["a3"]))
        lines = [x for x in logs.output if "check_codes " in x]
        self.assertEqual(lines, ["INFO:vision:check_codes v=3 a=MATCH n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110H;n"])
        self.assertFalse([x for x in logs.output if "vision_answer_fixed" in x])


class OwnerCaseTests(unittest.TestCase):
    """Criterion 2: the owner's live run of 11.10 (a worn baby bodysuit, the logo on the inner
    side), rebuilt from the decoded codes line (reports/owner-run-20261011-decode.md)."""
    OWNER_LINE = ("check_codes v=2 a=NEEDS_REVIEW n=8 codes=1MDD001C,2MDD101C,3MDD101C,4NNN110V,5NPP001W,"
                  "6MDD001C,7MDD001C,8APN100V;n")
    INV = [{"attribute_id": "body.colour", "claim": "цвет ткани: белый", "reference_roi": BIG},
           {"attribute_id": "neck.shape", "claim": "форма выреза: круглый", "reference_roi": SMALL},
           {"attribute_id": "snaps.bottom", "claim": "застёжка снизу: кнопки", "reference_roi": SMALL},
           {"attribute_id": "logo.inner", "claim": "логотип на изнанке: BRAND", "reference_roi": SMALL},
           {"attribute_id": "sleeve.length", "claim": "длина рукава: короткий", "reference_roi": BIG},
           {"attribute_id": "fabric.kind", "claim": "ткань: трикотаж", "reference_roi": BIG},
           {"attribute_id": "trim.edge", "claim": "кант: в тон", "reference_roi": BIG},
           {"attribute_id": "cuff.fold", "claim": "манжеты: отворот", "reference_roi": SMALL}]

    def per_order(self, logo_views, cuff_view=None):
        first = {"logo.inner": ("MISMATCH", "NONE", logo_views[0]), "sleeve.length": ("MATCH", "PARTIAL"),
                 "cuff.fold": ("MATCH", "PARTIAL")}
        second = {"logo.inner": ("MISMATCH", "NONE", logo_views[1]), "sleeve.length": ("MATCH", "PARTIAL"),
                  "cuff.fold": ("MISMATCH", "NONE", cuff_view)}
        return [(first, ()), (second, ())]

    def test_the_fixture_reproduces_the_recorded_codes(self):
        # without a view (the bot on 11.10) the same answers give the owner's codes, letter for letter
        self.assertEqual(line_of(self.INV, self.per_order((None, None))),
                         self.OWNER_LINE.replace("v=2", "v=3"))
        r = assess(self.INV, self.per_order((None, None)))
        self.assertEqual((r["status"], reasons(r)), ("NEEDS_REVIEW", {
            "logo.inner": "not_visible", "sleeve.length": "weak_evidence", "cuff.fold": "not_visible"}))
        self.assertIn("логотип на изнанке (не видно)", format_notes(r))       # what the owner objected to

    def test_with_the_logo_out_of_view_in_both_orders(self):
        per_order = self.per_order((OUT, OUT), cuff_view=OUT)     # the cuff: out of view in one order only
        r = assess(self.INV, per_order)
        self.assertEqual(r["status"], "NEEDS_REVIEW")              # #5 and #8 still need the seller's eyes
        self.assertNotIn("reason", r)
        self.assertEqual(r["not_compared"], [{"attribute_id": "logo.inner", "label": "логотип на изнанке"}])
        self.assertEqual(reasons(r), {"sleeve.length": "weak_evidence", "cuff.fold": "not_visible"})
        self.assertEqual(format_notes(r), "Совпадает: 5 из 8. Проверьте сами: длина рукава (не уверены), манжеты "
                                          "(не видно). Не сравнивалось (на ИИ-фото это место не видно): логотип на "
                                          "изнанке.")
        self.assertEqual(format_notes(r, "en"), "Matches: 5 of 8. Check yourself: длина рукава (not sure), манжеты "
                                                "(not visible). Not compared (this place is not visible on the AI "
                                                "photo): логотип на изнанке.")
        self.assertEqual(line_of(self.INV, per_order), "check_codes v=3 a=NEEDS_REVIEW n=8 codes=1MDD001C,2MDD101C,"
                                                       "3MDD101C,4NNN110H,5NPP001W,6MDD001C,7MDD001C,8APN100V;n")

    def test_the_owner_line_itself_still_reads(self):
        r = recompute.recompute([self.OWNER_LINE])
        self.assertEqual((r["lines"], r["bad"], r["a_diff"], r["inconsistent"]), (1, 0, 0, 0))
        self.assertEqual(r["rules"]["A"][None]["NEEDS_REVIEW"], 1)
        self.assertTrue(codes_log.valid(self.OWNER_LINE))


class NotesTests(unittest.TestCase):
    def result(self, **kw):
        base = {"status": "NEEDS_REVIEW", "total": 8, "confirmed": ["a"] * 5, "differs": [], "extra": "none",
                "to_check": [{"attribute_id": "w", "label": "цвет", "reason": "weak_evidence"}],
                "not_compared": [{"attribute_id": "l", "label": "логотип"}]}
        base.update(kw)
        return base

    def test_texts_in_both_languages(self):
        self.assertEqual(format_notes(self.result()),
                         "Совпадает: 5 из 8. Проверьте сами: цвет (не уверены). " + SKIP_RU + "логотип.")
        self.assertEqual(format_notes(self.result(), "en"),
                         "Matches: 5 of 8. Check yourself: цвет (not sure). " + SKIP_EN + "логотип.")

    def test_match_carries_only_the_neutral_line(self):
        r = self.result(status="MATCH", to_check=[])
        self.assertEqual(format_notes(r), SKIP_RU + "логотип.")
        self.assertEqual(format_notes(r, "en"), SKIP_EN + "логотип.")
        self.assertEqual(format_notes(self.result(status="MATCH", to_check=[], not_compared=[])), "")
        old = self.result(status="MATCH", to_check=[])
        del old["not_compared"]                                # a result built before this package
        self.assertEqual(format_notes(old), "")

    def test_an_out_of_view_item_is_never_listed_to_check(self):
        # even if a caller lists the same attribute in both places, the notes do not ask to check it
        r = self.result(to_check=[{"attribute_id": "l", "label": "логотип", "reason": "not_visible"}])
        for lang, check in (("ru", CHECK_RU), ("en", CHECK_EN)):
            self.assertNotIn(check, format_notes(r, lang))
        self.assertEqual(format_notes(r), "Совпадает: 5 из 8. " + SKIP_RU + "логотип.")

    def test_long_list_is_capped_and_the_reason_text_is_last(self):
        many = [{"attribute_id": str(i), "label": "д%d" % i} for i in range(9)]
        text = format_notes(self.result(to_check=[], not_compared=many, reason="too_few_compared"))
        self.assertIn("д5 и ещё 3.", text)
        self.assertNotIn("д6", text)
        self.assertTrue(text.endswith(FEW_RU))
        self.assertTrue(format_notes(self.result(to_check=[], reason="too_few_compared"), "en").endswith(FEW_EN))
        self.assertNotIn(FEW_RU, format_notes(self.result(reason="other")))

    def test_malformed_not_compared_gives_no_notes(self):
        for bad in ("x", [1], [{"label": "x"}], [{"attribute_id": "a"}]):
            self.assertEqual(format_notes(self.result(not_compared=bad)), "", bad)

    def test_the_bot_reply_under_match(self):
        from app.photo_checks import MESSAGES, PhotoChecks
        from tests.test_t4_vision import Session
        r = self.result(status="MATCH", to_check=[])
        checks = PhotoChecks(lambda a, b: Session(), lambda ref, cand: r)
        checks.handle({"user_id": 1, "chat_id": 1, "kind": "photo", "file_id": "g", "caption": None})
        reply = checks.handle({"user_id": 1, "chat_id": 1, "kind": "photo", "file_id": "r", "caption": "real"})[0]
        text = reply if isinstance(reply, str) else reply.text
        self.assertEqual(text, MESSAGES["MATCH"] + " " + SKIP_RU + "логотип.")


class RepairTests(unittest.TestCase):
    """REVISE 2: for a result whose view is exactly OUT_OF_VIEW, MATCH + NONE is read as
    MISMATCH + NONE and a missing candidate_roi is filled, so the check completes instead of
    ending UNAVAILABLE. Nothing else is repaired, and a repaired result is never confirmed."""
    BAD_BOXES = (None, [], [1, 2, 3], [1, 2, 3, 4, 5], "x", MARK, {"x": 1, "y": 2, "w": 3, "h": 4},
                 [1.5, 2, 3, 4], [True, 0, 10, 10], ["1", "2", "3", "4"], 7, Ellipsis)

    def setUp(self):
        self.inv = inventory(4, logo_at=3)
        self.v = vision_core.parse_inventory(chat({"attributes": self.inv}))

    def raw(self, a3=None, **fields):
        """One raw compare answer; a3 = (verdict, evidence, view) of the logo, `fields` set on it."""
        a = compare_answer(self.v["sha256"], {"a3": a3} if a3 else None, attrs=self.inv)
        for key, value in fields.items():
            if value is Ellipsis:
                a["results"][3].pop(key, None)
            else:
                a["results"][3][key] = value
        return a

    @staticmethod
    def repairs(fixes):
        """The fix list without VIEW_DEFAULTED (these bare answers carry a view on the logo only)."""
        return [f for f in fixes if f[0] != "VIEW_DEFAULTED"]

    def parse(self, answer):
        fixes = []
        return vision_core.parse_compare(chat(answer), self.v, fixes), self.repairs(fixes)

    def rejected(self, answer):
        fixes = []
        with self.assertRaises(AnswerError) as ctx:
            vision_core.parse_compare(chat(answer), self.v, fixes)
        return ctx.exception.rule, self.repairs(fixes)

    def assess(self, first, second):
        return vision_core.assess(self.v, self.parse(first)[0], self.parse(second)[0])

    def test_a_match_with_no_evidence_out_of_view_becomes_mismatch(self):
        out, fixes = self.parse(self.raw(("MATCH", "NONE", OUT)))
        self.assertEqual(fixes, [("VIEW_VERDICT_FIXED", 1)])
        self.assertEqual((out["results"][3]["verdict"], out["results"][3]["evidence_quality"]), ("MISMATCH", "NONE"))
        self.assertEqual(out["candidate_view"]["a3"], OUT)
        self.assertEqual([r["verdict"] for r in out["results"][:3]], ["MATCH"] * 3)      # the others untouched

    def test_a_both_orders_agree_is_out_of_view_one_order_is_not_visible(self):
        bad = self.raw(("MATCH", "NONE", OUT))
        r = self.assess(bad, self.raw(("MATCH", "NONE", OUT)))
        self.assertEqual((r["status"], ids(r["not_compared"]), r["confirmed"]), ("MATCH", ["a3"], ["a0", "a1", "a2"]))
        r = self.assess(bad, self.raw(HIDDEN))
        self.assertEqual((r["status"], ids(r["not_compared"])), ("MATCH", ["a3"]))
        # the other order saw nothing either but calls the place in view: to check, as today
        for other in (self.raw(("MISMATCH", "NONE", IN)), self.raw(("MISMATCH", "NONE"))):
            for pair in ((bad, other), (other, bad)):
                r = self.assess(*pair)
                self.assertEqual((r["status"], reasons(r), r["not_compared"]),
                                 ("NEEDS_REVIEW", {"a3": "not_visible"}, []))
        # the other order confirms it with DIRECT evidence: still never confirmed
        for pair in ((bad, self.raw()), (self.raw(), bad), (bad, self.raw(("MATCH", "DIRECT", OUT)))):
            r = self.assess(*pair)
            self.assertEqual((r["status"], reasons(r), r["confirmed"]),
                             ("NEEDS_REVIEW", {"a3": "not_visible"}, ["a0", "a1", "a2"]))

    def test_a_only_that_exact_pair(self):
        # IN_VIEW, no view, a junk view: MATCH + NONE is still T2's reject
        for view in (IN, None, MARK, "out_of_view"):
            rule, fixes = self.rejected(self.raw(("MATCH", "NONE", view)))
            self.assertEqual(rule, "VERDICT_EVIDENCE", view)
            self.assertNotIn("VIEW_VERDICT_FIXED", dict(fixes))
        # OUT_OF_VIEW with other pairs: nothing is rewritten
        for pair, cls in ((("MATCH", "DIRECT"), None), (("MATCH", "PARTIAL"), "fine_detail_unverified"),
                          (("MISMATCH", "PARTIAL"), "fine_detail_unverified"), (("MISMATCH", "NONE"), None),
                          (("NOT_VISIBLE_IN_REFERENCE", "NONE"), None)):
            answer = self.raw(pair + (OUT,))
            out, fixes = self.parse(answer)
            self.assertEqual(fixes, [], pair)
            self.assertEqual((out["results"][3]["verdict"], out["results"][3]["evidence_quality"]), pair)
            if cls:
                self.assertEqual(reasons(self.assess(answer, self.raw(pair + (OUT,)))), {"a3": cls})
        r = self.assess(self.raw(("MATCH", "DIRECT", OUT)), self.raw(("MATCH", "DIRECT", OUT)))
        self.assertEqual((r["status"], r["confirmed"], r["not_compared"]), ("MATCH", ["a0", "a1", "a2", "a3"], []))
        rule, fixes = self.rejected(self.raw(("NOT_VISIBLE_IN_REFERENCE", "DIRECT", OUT)))
        self.assertEqual((rule, fixes), ("VERDICT_EVIDENCE", []))

    def test_b_a_missing_box_is_filled_from_the_reference_box(self):
        reference = self.inv[3]["reference_roi"]
        for box in self.BAD_BOXES:
            with self.subTest(box=box):
                out, fixes = self.parse(self.raw(HIDDEN, candidate_roi=box))
                self.assertEqual(fixes, [("VIEW_ROI_FILLED", 1)])
                self.assertEqual(out["results"][3]["candidate_roi"], reference)
                self.assertEqual(out["results"][3]["reference_roi"], reference)
                self.assertNotIn(MARK, repr(out) + repr(fixes))
        r = self.assess(self.raw(HIDDEN, candidate_roi=None), self.raw(HIDDEN, candidate_roi=None))
        self.assertEqual((r["status"], ids(r["not_compared"])), ("MATCH", ["a3"]))
        r = self.assess(self.raw(HIDDEN, candidate_roi=None), self.raw(("MISMATCH", "NONE", IN)))
        self.assertEqual((r["status"], reasons(r), r["not_compared"]), ("NEEDS_REVIEW", {"a3": "not_visible"}, []))

    def test_b_the_full_frame_when_there_is_no_reference_box(self):
        nv = ("NOT_VISIBLE_IN_REFERENCE", "NONE", OUT)             # reference_roi is null for this verdict
        out, fixes = self.parse(self.raw(nv, candidate_roi=None))
        self.assertEqual(fixes, [("VIEW_ROI_FILLED", 1)])
        self.assertEqual((out["results"][3]["candidate_roi"], out["results"][3]["reference_roi"]),
                         ([0, 0, 1000, 1000], None))
        r = self.assess(self.raw(nv, candidate_roi=None), self.raw(nv, candidate_roi=None))
        self.assertEqual((r["status"], ids(r["not_compared"])), ("MATCH", ["a3"]))

    def test_b_never_touches_the_reference_box(self):
        # a broken reference_roi is still T2's reject; the candidate box got the full frame meanwhile
        for reference in (None, [1, 2, 3], "x"):
            rule, fixes = self.rejected(self.raw(HIDDEN, candidate_roi=None, reference_roi=reference))
            self.assertEqual((rule, fixes), ("ROI_SHAPE", [("VIEW_ROI_FILLED", 1)]), reference)
        rule, fixes = self.rejected(self.raw(HIDDEN, reference_roi=None))      # candidate box fine: no repair
        self.assertEqual((rule, fixes), ("ROI_SHAPE", []))

    def test_b_only_for_out_of_view_with_no_evidence(self):
        for a3 in (("MISMATCH", "NONE", IN), ("MISMATCH", "NONE"), ("MISMATCH", "NONE", MARK),      # not OUT_OF_VIEW
                   ("MISMATCH", "DIRECT", OUT), ("MATCH", "DIRECT", OUT), ("MATCH", "PARTIAL", OUT),
                   ("MISMATCH", "PARTIAL", OUT), ("MATCH", "DIRECT", IN)):                         # evidence
            for box in (None, [], "x"):
                rule, fixes = self.rejected(self.raw(a3, candidate_roi=box))
                self.assertEqual((rule, fixes), ("ROI_SHAPE", []), (a3, box))

    def test_b_a_box_of_four_integers_is_left_alone(self):
        own = [10, 20, 300, 400]
        out, fixes = self.parse(self.raw(HIDDEN, candidate_roi=own))
        self.assertEqual((out["results"][3]["candidate_roi"], fixes), (own, []))
        out, fixes = self.parse(self.raw(HIDDEN, candidate_roi=[900, 900, 300, 300]))     # the clamp's case, not ours
        self.assertEqual((out["results"][3]["candidate_roi"], fixes), ([900, 900, 100, 100], [("ROI_CLAMPED", 1)]))
        rule, fixes = self.rejected(self.raw(HIDDEN, candidate_roi=[0, 0, 0, 5]))         # empty box: T2 decides
        self.assertEqual((rule, fixes), ("ROI_OUT_OF_FRAME", []))

    def test_both_repairs_on_one_result(self):
        out, fixes = self.parse(self.raw(("MATCH", "NONE", OUT), candidate_roi=None))
        self.assertEqual(fixes, [("VIEW_VERDICT_FIXED", 1), ("VIEW_ROI_FILLED", 1)])
        self.assertEqual((out["results"][3]["verdict"], out["results"][3]["candidate_roi"]),
                         ("MISMATCH", self.inv[3]["reference_roi"]))

    def test_malformed_results_are_left_to_t2(self):
        for results in (None, "x", [1, None], [[OUT]]):
            a = self.raw()
            a["results"] = results
            with self.subTest(results=results), self.assertRaises(AnswerError):
                vision_core.parse_compare(chat(a), self.v)
        a = self.raw(("MATCH", "NONE", OUT))
        a["results"][3]["evidence_quality"] = ["NONE"]            # not the enum string: no repair
        rule, fixes = self.rejected(a)
        self.assertEqual((rule, fixes), ("VERDICT_EVIDENCE", []))

    def test_through_the_chain_the_check_completes_and_is_counted(self):
        def broken(index, answer):                                # what the controls saw on Set B
            item = answer["results"][3]
            item.update(candidate_view=OUT, verdict="MATCH", evidence_quality="NONE", candidate_roi=None,
                        observation=MARK)
        tally = []
        compare = vision_compare.compare_from_env(ENV, transport=FakeModel(inventory=self.inv, mutate=broken),
                                                  on_reject=lambda *a: tally.append(a))
        with self.assertLogs("vision", "INFO") as logs:
            result = compare(JPEG, PNG)
        self.assertEqual((result["status"], ids(result["not_compared"]), result["confirmed"]),
                         ("MATCH", ["a3"], ["a0", "a1", "a2"]))
        text = "\n".join(logs.output)
        for stage in ("compare_a", "compare_b"):
            self.assertIn("vision_answer_fixed stage=%s rule=VIEW_VERDICT_FIXED n=1" % stage, text)
            self.assertIn("vision_answer_fixed stage=%s rule=VIEW_ROI_FILLED n=1" % stage, text)
            self.assertIn((stage, "FIXED", "VIEW_VERDICT_FIXED"), tally)
            self.assertIn((stage, "FIXED", "VIEW_ROI_FILLED"), tally)
        self.assertIn("check_codes v=3 a=MATCH n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110H;n", text)
        self.assertNotIn("vision_bad_answer", text)
        self.assertNotIn(MARK, text + repr(result))
        self.assertEqual(format_notes(result), SKIP_RU + "логотип на изнанке.")

    def test_the_same_broken_answer_in_view_is_still_unavailable(self):
        def broken(index, answer):
            answer["results"][3].update(candidate_view=IN, verdict="MATCH", evidence_quality="NONE")
        compare = vision_compare.compare_from_env(ENV, transport=FakeModel(inventory=self.inv, mutate=broken))
        with self.assertLogs("vision", "WARNING") as logs:
            self.assertEqual(compare(JPEG, PNG), {"status": "UNAVAILABLE"})
        self.assertIn("rule=VERDICT_EVIDENCE path=results[3]", logs.output[0])

    def test_random_broken_answers_a_repaired_result_is_never_confirmed(self):
        """Random answers with the two defects planted at random: an answer without a defect on an
        OUT_OF_VIEW + NONE result parses exactly as before; whatever was repaired never counts as
        confirmed and never turns into a difference."""
        rng = random.Random(12)
        seen = {"completed": 0, "rejected": 0, "hidden": 0}
        hidden_pairs = [("MATCH", "NONE"), ("MISMATCH", "NONE"), ("NOT_VISIBLE_IN_REFERENCE", "NONE")]
        for _ in range(1500):
            n = rng.randint(3, 8)
            inv = inventory(n)
            v = vision_core.parse_inventory(chat({"attributes": inv}))
            answers, repaired, must_reject = [], set(), False
            for _order in range(2):
                a = compare_answer(v["sha256"], attrs=inv)
                for item in a["results"]:
                    view = rng.choice((IN, OUT, OUT, None))
                    if view == OUT and rng.random() < 0.4:      # a detail the model calls hidden
                        verdict, evidence = rng.choice(hidden_pairs)
                    elif rng.random() < 0.02:                   # the forbidden pair anywhere
                        verdict, evidence = "MATCH", "NONE"
                    else:
                        verdict, evidence = rng.choice(PAIRS) if rng.random() < 0.3 else PAIRS[0]
                    item.update(verdict=verdict, evidence_quality=evidence,
                                reference_roi=None if verdict == "NOT_VISIBLE_IN_REFERENCE" else item["reference_roi"])
                    if view:
                        item["candidate_view"] = view
                    fixable = view == OUT and evidence == "NONE"
                    no_box = rng.random() < (0.5 if fixable else 0.02)
                    if no_box:
                        item["candidate_roi"] = rng.choice((None, [], "x"))
                    if (verdict, evidence) == ("MATCH", "NONE") or no_box:
                        if fixable:
                            repaired.add(item["attribute_id"])
                        else:
                            must_reject = True
                answers.append(a)
            try:
                first, second = [vision_core.parse_compare(chat(a), v) for a in answers]
            except AnswerError:
                self.assertTrue(must_reject)               # a repair never causes a reject of its own
                seen["rejected"] += 1
                continue
            self.assertFalse(must_reject)                  # a defect outside OUT_OF_VIEW + NONE is never repaired
            result = vision_core.assess(v, first, second)
            self.assertFalse(repaired & set(result["confirmed"]))
            self.assertFalse(repaired & set(ids(result["differs"])))
            for aid in repaired:
                self.assertIn(aid, ids(result["not_compared"]) + ids(result["to_check"]))
            seen["completed"] += bool(repaired)
            seen["hidden"] += bool(repaired & set(ids(result["not_compared"])))
        self.assertGreater(min(seen.values()), 20, seen)


class PrivacyTests(unittest.TestCase):
    """Criterion 3: a view outside the enum never reaches a log, the record or the notes."""

    def test_marker_as_a_view_never_leaves_the_parser(self):
        inv = inventory(4)

        def junk(index, answer):
            for r in answer["results"]:
                r["candidate_view"] = MARK if r["attribute_id"] != "a3" else [MARK, {"x": MARK}]
        got = []
        model = FakeModel(inventory=inv, per_order=both({"a3": ("MISMATCH", "NONE")}), mutate=junk)
        compare = vision_compare.compare_from_env(ENV, transport=model, on_codes=got.append)
        with self.assertLogs("vision", "DEBUG") as logs:
            result = compare(JPEG, PNG)
        text = "\n".join(logs.output)
        self.assertNotIn(MARK, text + repr(result) + repr(got) + format_notes(result) + format_notes(result, "en"))
        self.assertEqual((result["status"], reasons(result), result["not_compared"]),
                         ("NEEDS_REVIEW", {"a3": "not_visible"}, []))        # nothing hidden from the seller
        for stage in ("compare_a", "compare_b"):
            self.assertIn("vision_answer_fixed stage=%s rule=VIEW_DEFAULTED n=4" % stage, text)
        self.assertIn("codes=1MDD001C,2MDD001C,3MDD001C,4NNN000V;n", text)

    def test_a_model_without_the_field_behaves_as_before_and_is_counted(self):
        tally = []
        model = FakeModel(views=False)
        compare = vision_compare.compare_from_env(ENV, transport=model,
                                                  on_reject=lambda *a: tally.append(a))
        with self.assertLogs("vision", "INFO") as logs:
            self.assertEqual(compare(JPEG, PNG)["status"], "MATCH")
        fixed = [x for x in logs.output if "vision_answer_fixed" in x]
        self.assertEqual(fixed, ["INFO:vision:vision_answer_fixed stage=compare_a rule=VIEW_DEFAULTED n=3",
                                 "INFO:vision:vision_answer_fixed stage=compare_b rule=VIEW_DEFAULTED n=3"])
        self.assertEqual(tally, [("compare_a", "FIXED", "VIEW_DEFAULTED"), ("compare_b", "FIXED", "VIEW_DEFAULTED")])

    def test_the_record_and_the_line_carry_no_view_text(self):
        line = line_of(inventory(4), both({"a3": HIDDEN}))
        self.assertRegex(line, r"^check_codes v=3 a=MATCH n=4 codes=[0-9A-Za-z,;]+$")
        self.assertNotIn("VIEW", line)


# ---------------------------------------------------------------------------
# Criterion 4: with every view IN_VIEW, rule A' is rule A, except for the MATCH minimum.

def main_assess(validated, first, second):
    """`assess` as it stood on main f157b05 (rule A), rebuilt from T2 alone as the reference."""
    aggregate = t2.aggregate_responses(first, second)
    inv = {a["attribute_id"]: a for a in validated["inventory"]["attributes"]}
    left = {r["attribute_id"]: r for r in first["results"]}
    right = {r["attribute_id"]: r for r in second["results"]}
    confirmed, differs, to_check = [], [], []
    for aid in validated["attribute_ids"]:
        outcome = aggregate["attributes"][aid]
        roi = inv[aid]["reference_roi"]
        fine = roi[2] * roi[3] < 20000
        evidence = (left[aid]["evidence_quality"], right[aid]["evidence_quality"])
        if outcome == "MATCH":
            confirmed.append(aid)
        elif outcome == "MISMATCH":
            differs.append(aid)
        elif outcome == "NOT_VISIBLE_IN_REFERENCE" or "NONE" in evidence:
            to_check.append((aid, "not_visible"))
        elif aid in aggregate["escalations"] and fine:
            to_check.append((aid, "fine_detail_unverified"))
        elif outcome == "ASYMMETRIC_PASS":
            to_check.append((aid, "orders_disagree"))
        else:
            to_check.append((aid, "weak_evidence"))
    extra = {"MISMATCH": "confirmed", "NEEDS_REVIEW": "unconfirmed"}.get(aggregate["coverage"], "none")
    if differs or extra == "confirmed":
        status = "MISMATCH"
    elif to_check or extra == "unconfirmed":
        status = "NEEDS_REVIEW"
    else:
        status = "MATCH"
    return {"status": status, "confirmed": confirmed, "differs": differs, "to_check": to_check, "extra": extra}


PAIRS = [("MATCH", "DIRECT"), ("MATCH", "PARTIAL"), ("MISMATCH", "DIRECT"), ("MISMATCH", "PARTIAL"),
         ("MISMATCH", "NONE"), ("NOT_VISIBLE_IN_REFERENCE", "PARTIAL"), ("NOT_VISIBLE_IN_REFERENCE", "NONE")]
ELEMENT = {"candidate_roi": [300, 300, 200, 200], "description": "extra pocket",
           "visibility_in_reference": "VISIBLE_AND_ABSENT"}


def random_case(rng, views):
    """(inventory, per_order): a random inventory of 1..12 attributes and two random valid answers.
    `views` picks the candidate_view of one result."""
    n = rng.randint(1, 12)
    inv = [{"attribute_id": "a%d" % i, "claim": "%s: как на фото" % NAMES[i],
            "reference_roi": rng.choice((BIG, SMALL))} for i in range(n)]
    mostly_match = rng.random() < 0.7        # enough all-confirmed cases to reach MATCH often
    per_order = []
    for _ in range(2):
        overrides = {}
        for a in inv:
            pair = PAIRS[0] if mostly_match and rng.random() < 0.9 else rng.choice(PAIRS)
            overrides[a["attribute_id"]] = pair + (views(rng),)
        unmapped = (ELEMENT,) if rng.random() < 0.08 else ()
        per_order.append((overrides, unmapped))
    if rng.random() < 0.5:                   # the same answer in both orders, views included
        per_order[1] = per_order[0]
    return inv, per_order


class PropertyTests(unittest.TestCase):
    def compare(self, inv, per_order):
        v, answers = parsed(inv, per_order)
        new = vision_core.assess(v, *answers)
        bare = [{k: x for k, x in a.items() if k != "candidate_view"} for a in answers]
        return main_assess(v, *bare), new

    def test_all_in_view_equals_main_except_for_the_match_minimum(self):
        rng = random.Random(20261011)
        exceptions, matches = set(), 0
        for _ in range(1500):
            inv, per_order = random_case(rng, lambda r: r.choice((IN, None)))    # IN_VIEW, said or defaulted
            old, new = self.compare(inv, per_order)
            self.assertEqual(new["not_compared"], [])
            self.assertEqual(new["confirmed"], old["confirmed"])
            self.assertEqual(ids(new["differs"]), old["differs"])
            self.assertEqual([(i["attribute_id"], i["reason"]) for i in new["to_check"]], old["to_check"])
            self.assertEqual(new["extra"], old["extra"])
            if new["status"] == old["status"]:
                self.assertNotIn("reason", new)
                matches += old["status"] == "MATCH"
                continue
            # the ONLY difference: main said MATCH on an inventory of fewer than 3 attributes
            self.assertEqual((old["status"], new["status"], new.get("reason")),
                             ("MATCH", "NEEDS_REVIEW", "too_few_compared"))
            exceptions.add(len(inv))
        self.assertEqual(exceptions, {1, 2})
        self.assertGreater(matches, 100)

    def test_random_views_only_ever_move_both_order_hidden_details(self):
        rng = random.Random(4)
        seen = {"hidden": 0, "match_gained": 0, "few": 0}
        for _ in range(2500):
            inv, per_order = random_case(rng, lambda r: r.choice((IN, OUT, OUT, None, MARK)))
            old, new = self.compare(inv, per_order)
            hidden = ids(new["not_compared"])
            for aid in hidden:                               # the definition, checked on the raw answers
                for overrides, _ in per_order:
                    verdict, evidence, view = overrides[aid]
                    self.assertEqual((evidence, view), ("NONE", OUT))
                    self.assertNotEqual(verdict, "MATCH")
            self.assertFalse(set(hidden) & set(ids(new["to_check"])))
            # confirmed / differs / extra are never touched; only "not visible" items can move out
            self.assertEqual((new["confirmed"], ids(new["differs"]), new["extra"]),
                             (old["confirmed"], old["differs"], old["extra"]))
            self.assertEqual(sorted(old["to_check"]),
                             sorted([(i["attribute_id"], i["reason"]) for i in new["to_check"]]
                                    + [(aid, "not_visible") for aid in hidden]))
            # MISMATCH is exactly main's MISMATCH: nothing out of view hides or creates one
            self.assertEqual(new["status"] == "MISMATCH", old["status"] == "MISMATCH")
            if new["status"] == "MATCH":
                self.assertGreaterEqual(len(new["confirmed"]), 3)
                self.assertGreaterEqual(2 * len(new["confirmed"]), len(inv))
                self.assertFalse(new["to_check"])
                if old["status"] != "MATCH":
                    self.assertTrue(hidden)
                    seen["match_gained"] += 1
            seen["hidden"] += bool(hidden)
            seen["few"] += new.get("reason") == "too_few_compared"
            self.assertNotIn(MARK, repr(new))
        self.assertGreater(min(seen.values()), 20, seen)


if __name__ == "__main__":
    unittest.main()
