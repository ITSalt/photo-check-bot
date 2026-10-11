"""WP-CONTROLS-02: text/logo/count flag (D-2, D-5), code record v=2, rule B in the recompute.

Fake transports only. No network, no keys, no provider calls.
"""
import hashlib
import io
import json
import logging
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from app import recompute, vision_compare, vision_core
from app.photo_compare import run_bot_pair
from tests.test_t4_vision import (ENV, EXTRA, JPEG, PNG, FakeModel, Session, chat, compare_answer)

MARK = "ZQMARKERZQ"
RULE_SHA = "2ca36df28ba6edba6da3befeacc00f4a68f84504e473500541d3f07c54d40eeb"

BIG = [100, 100, 300, 400]            # not fine
SMALL = [500, 600, 100, 100]          # fine (area < FINE_ROI_AREA)
COARSE = [{"attribute_id": "body.colour", "claim": "цвет корпуса: синий", "reference_roi": BIG},
          {"attribute_id": "collar.shape", "claim": "форма воротника: круглый", "reference_roi": BIG},
          {"attribute_id": "sleeve.length", "claim": "длина рукава: короткий", "reference_roi": BIG}]
PARTIAL = ("MATCH", "PARTIAL")


def record_of(per_order, inventory):
    v = vision_core.parse_inventory(chat({"attributes": inventory}))
    a, b = [vision_core.parse_compare(chat(compare_answer(v["sha256"], o, u, attrs=inventory)), v)
            for o, u in per_order]
    return vision_core.code_record(v, a, b), vision_core.assess(v, a, b)


def b_of(per_order, inventory):
    """(A status, B status) recomputed from the logged line alone."""
    record, _ = record_of(per_order, inventory)
    text = vision_core.codes_line(record)
    r = recompute.recompute([text])
    out = {}
    for rule in ("A'", "B"):          # a v=3 line: A' is its production rule, B is built on it
        out[rule] = [s for s, n in r["rules"][rule][None].items() if n][0]
    return out["A'"], out["B"], r


class RuleObjectTests(unittest.TestCase):
    def test_rule_object_hash(self):
        canon = json.dumps(vision_core.TEXT_LOGO_COUNT_RULE, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(canon).hexdigest(), RULE_SHA)

    def test_regex_is_built_from_the_rule_object(self):
        rule = vision_core.TEXT_LOGO_COUNT_RULE
        for stem in rule["stems_substring"] + rule["stems_word_start"] + rule["words_whole"]:
            self.assertTrue(vision_core.text_logo_count(stem), stem)


class ClaimClassTests(unittest.TestCase):
    TRUE = ["два кармана", "3 пуговицы", "логотип на коже", "label sewn in", "two pockets",
            "Надпись на ремешке", "ТЕКСТ: BRAND", "пара швов", "pair of rivets", "четыре заклёпки",
            "both sleeves", "обе манжеты", "single stitch", "серийный номер 123", "buckle 2x",
            "text on the strap", "print: floral", "марка на подошве", "число отверстий", "штук в ряду",
            "the number of eyelets", "quantity of studs", "brand mark", "qty: 4 holes"]
    FALSE = ["текстиль", "у отметки 3 часов", "кожаный ремешок", "двойной шов", "textile",
             "at 3 o'clock", "position 5 on the dial", "цвет корпуса: синий", "round collar",
             "положение 12 рядом с отметкой", "smooth leather", "дверца", "кожа", "", "ile"]

    def test_true_classes(self):
        for claim in self.TRUE:
            self.assertTrue(vision_core.text_logo_count(claim), claim)

    def test_false_classes(self):
        for claim in self.FALSE:
            self.assertFalse(vision_core.text_logo_count(claim), claim)

    def test_not_followed_by(self):
        self.assertFalse(vision_core.text_logo_count("textile"))
        self.assertFalse(vision_core.text_logo_count("текстиль"))
        self.assertTrue(vision_core.text_logo_count("text"))
        self.assertTrue(vision_core.text_logo_count("текст"))
        self.assertTrue(vision_core.text_logo_count("текстура и текст"))

    def test_word_start_and_whole_word_bounds(self):
        self.assertTrue(vision_core.text_logo_count("пара"))
        self.assertFalse(vision_core.text_logo_count("упаровка"))      # word start only
        self.assertTrue(vision_core.text_logo_count("два"))
        self.assertFalse(vision_core.text_logo_count("дважды"))         # whole word only
        self.assertFalse(vision_core.text_logo_count("alltwo"))
        self.assertTrue(vision_core.text_logo_count("a pair"))

    def test_digits_and_exemption_window(self):
        self.assertTrue(vision_core.text_logo_count("pocket 7"))
        self.assertFalse(vision_core.text_logo_count("at 3 o'clock"))
        self.assertFalse(vision_core.text_logo_count("3 a b час"))           # 3 tokens away
        self.assertTrue(vision_core.text_logo_count("3 a b c час"))          # 4 tokens away
        self.assertFalse(vision_core.text_logo_count("час 4"))
        self.assertFalse(vision_core.text_logo_count("position 12"))
        self.assertTrue(vision_core.text_logo_count("9 a b c position"))
        self.assertFalse(vision_core.text_logo_count("3 o'clock and 7"))     # 7 is 3 tokens away


    def test_case_insensitive(self):
        self.assertTrue(vision_core.text_logo_count("LOGO"))
        self.assertTrue(vision_core.text_logo_count("ЛОГОТИП"))
        self.assertTrue(vision_core.text_logo_count("TWO pockets"))


class RecordV2Tests(unittest.TestCase):
    def test_flag_and_both_match_in_the_record(self):
        inv = [COARSE[0], {**COARSE[1], "claim": "два кармана"}, {**COARSE[0], "attribute_id": "x.small",
                                                                   "reference_roi": SMALL}]
        record, _ = record_of([(None, ())] * 2, inv)
        self.assertEqual([a["tlc"] for a in record["attrs"]], [False, True, False])
        self.assertEqual([a["fine"] for a in record["attrs"]], [False, False, True])
        self.assertEqual([a["both_match"] for a in record["attrs"]], [True, True, True])
        self.assertEqual(vision_core.format_codes(record), "1MDD001C,2MDD011C,3MDD101C;n")
        self.assertTrue(vision_core.codes_line(record).startswith("check_codes v=3 a=MATCH n=3 codes="))

    def test_both_match_false_on_mismatch_and_asymmetry(self):
        diff = ("MISMATCH", "PARTIAL")
        record, _ = record_of([({"body.colour": diff}, ())] * 2, COARSE)
        self.assertFalse(record["attrs"][0]["both_match"])
        record, _ = record_of([(None, ()), ({"body.colour": diff}, ())], COARSE)
        self.assertFalse(record["attrs"][0]["both_match"])

    def test_cap_with_twelve_attributes(self):
        attrs = [{"pos": i, "outcome": "ASYMMETRIC_PASS", "evidence": ("PARTIAL", "NONE"), "fine": True,
                  "tlc": True, "both_match": True, "cls": "fine_detail_unverified"} for i in range(1, 13)]
        codes = vision_core.format_codes({"a": "MATCH", "extra": "unconfirmed", "attrs": attrs})
        self.assertTrue(codes.endswith(";u"))
        self.assertEqual(codes.count(","), 11)
        self.assertLessEqual(len(codes), vision_core.CODES_MAX)


class RuleBTests(unittest.TestCase):
    def test_b_confirms_coarse_partial_match(self):
        a, b, r = b_of([({"body.colour": PARTIAL, "collar.shape": PARTIAL}, ())] * 2, COARSE)
        self.assertEqual((a, b), ("NEEDS_REVIEW", "MATCH"))
        self.assertEqual((r["a_diff"], r["bad"], r["inconsistent"], r["v1"]), (0, 0, 0, 0))

    def test_b_not_for_a_fine_attribute(self):
        inv = [{**COARSE[0], "reference_roi": SMALL}, COARSE[1]]
        a, b, _ = b_of([({"body.colour": PARTIAL}, ())] * 2, inv)
        self.assertEqual((a, b), ("NEEDS_REVIEW", "NEEDS_REVIEW"))

    def test_b_not_for_a_flagged_attribute(self):
        for claim in ("логотип: синий", "два кармана", "3 пуговицы", "label: blue"):
            inv = [{**COARSE[0], "claim": claim}, COARSE[1]]
            a, b, _ = b_of([({"body.colour": PARTIAL}, ())] * 2, inv)
            self.assertEqual((a, b), ("NEEDS_REVIEW", "NEEDS_REVIEW"), claim)

    def test_b_needs_match_in_both_orders(self):
        other = ("MISMATCH", "PARTIAL")
        a, b, _ = b_of([({"body.colour": PARTIAL}, ()), ({"body.colour": other}, ())], COARSE)
        self.assertEqual(b, "NEEDS_REVIEW")
        a, b, _ = b_of([({"body.colour": other}, ())] * 2, COARSE)      # MISMATCH with PARTIAL: not a match
        self.assertEqual(b, "NEEDS_REVIEW")

    def test_b_needs_evidence_partial_or_better(self):
        # MATCH with NONE evidence is refused by T2 validation, so test the rule on codes
        # (the recompute must not trust it either): MATCH in both orders, evidence NONE in one.
        line = "check_codes v=2 a=NEEDS_REVIEW n=1 codes=1VPN001V;n"
        self.assertEqual(recompute.recompute([line])["rules"]["B"][None]["NEEDS_REVIEW"], 1)
        attrs, extra = recompute.parse_attrs("2", 1, "1NPN001V;n")
        self.assertEqual(recompute.rule_b(attrs, extra), "NEEDS_REVIEW")
        attrs, extra = recompute.parse_attrs("2", 1, "1NPP001W;n")
        self.assertEqual(recompute.rule_b(attrs, extra), "MATCH")

    def test_b_keeps_mismatch_and_extra_element(self):
        diff = ("MISMATCH", "DIRECT")
        a, b, _ = b_of([({"body.colour": PARTIAL, "collar.shape": diff}, ())] * 2, COARSE)
        self.assertEqual((a, b), ("MISMATCH", "MISMATCH"))
        a, b, _ = b_of([({"body.colour": PARTIAL}, (EXTRA,))] * 2, COARSE)
        self.assertEqual((a, b), ("MISMATCH", "MISMATCH"))
        a, b, _ = b_of([({"body.colour": PARTIAL}, (EXTRA,)), ({"body.colour": PARTIAL}, ())], COARSE)
        self.assertEqual((a, b), ("NEEDS_REVIEW", "NEEDS_REVIEW"))

    def test_b_equals_a_when_everything_is_direct(self):
        a, b, _ = b_of([(None, ())] * 2, COARSE)
        self.assertEqual((a, b), ("MATCH", "MATCH"))

    def test_b_and_c_keep_the_extra_outcome(self):
        text = "check_codes v=2 a=MISMATCH n=2 codes=1NPP001W,2MDD001C;c"
        r = recompute.recompute(["kind=same " + text])
        self.assertEqual(r["rules"]["B"]["same"]["MISMATCH"], 1)
        self.assertEqual(r["rules"]["C"]["same"]["MISMATCH"], 1)

    def test_v1_lines_still_read_b_is_na(self):
        v1 = ["kind=same check_codes v=1 a=MATCH n=2 codes=1MDD0C,2MDD0C;n",
              "kind=recrop check_codes v=1 a=NEEDS_REVIEW n=2 codes=1NPP0W,2MDD0C;n"]
        r = recompute.recompute(v1)
        self.assertEqual((r["lines"], r["bad"], r["a_diff"], r["v1"]), (2, 0, 0, 2))
        self.assertEqual(r["rules"]["A"]["same"]["MATCH"], 1)
        self.assertEqual(r["rules"]["C"]["recrop"]["NEEDS_REVIEW"], 1)
        out = "\n".join(recompute.report(r))
        self.assertIn("rule B: n/a (no check_codes v=2 or v=3 lines)", out)
        self.assertIn("v=1 lines left out of rule B (no flag): 2", out)

    def test_mixed_v1_and_v2_b_counts_v2_only(self):
        lines = ["kind=same check_codes v=1 a=NEEDS_REVIEW n=1 codes=1NPP0W;n",
                 "kind=same check_codes v=2 a=NEEDS_REVIEW n=1 codes=1NPP001W;n"]
        r = recompute.recompute(lines)
        self.assertEqual(r["rules"]["B"]["same"]["MATCH"], 1)
        self.assertEqual(r["rules"]["A"]["same"]["NEEDS_REVIEW"], 2)
        out = "\n".join(recompute.report(r))
        self.assertIn("rule B: positive MATCH 100.0% (runs=1)", out)

    def test_unavailable_run_counts_against_b(self):
        r = recompute.recompute(["kind=same check_codes v=2 a=NEEDS_REVIEW n=1 codes=1NPP001W;n",
                                 "kind=same unavailable"])
        out = "\n".join(recompute.report(r))
        self.assertIn("rule B: positive MATCH 50.0% (runs=2)", out)


class RecomputeCarryOverTests(unittest.TestCase):
    OK = "check_codes v=2 a=MATCH n=1 codes=1MDD001C;n"

    def test_check_codes_error_is_skipped_and_counted(self):
        lines = ["ERROR vision check_codes_error code=RECORD", self.OK]
        r = recompute.recompute(lines)
        self.assertEqual((r["lines"], r["bad"], r["errors"]), (1, 0, 1))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.txt")
            with open(p, "w") as f:
                f.write("\n".join(lines))
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(recompute._main([p]), 0)
        self.assertIn("check_codes_error lines (record not written, skipped): 1", buf.getvalue())
        self.assertNotIn("unparsable", buf.getvalue())

    def test_a_garbled_codes_line_is_still_bad(self):
        r = recompute.recompute(["check_codes v=2 a=MATCH n=1 codes=1MDD0C;n"])   # v=1 token under v=2
        self.assertEqual(r["bad"], 1)

    def test_shares_one_decimal_half_up(self):
        def share(num, den):
            lines = ["kind=same " + self.OK] * num + ["kind=same unavailable"] * (den - num)
            return [x for x in recompute.report(recompute.recompute(lines)) if x.startswith("rule A:")][0]
        self.assertIn("positive MATCH 12.5% (runs=8)", share(1, 8))
        self.assertIn("positive MATCH 66.7% (runs=3)", share(2, 3))
        self.assertIn("positive MATCH 6.3% (runs=16)", share(1, 16))     # 6.25 -> half up
        self.assertIn("positive MATCH 97.0% (runs=66)", share(64, 66))
        self.assertIn("positive MATCH 100.0% (runs=2)", share(2, 2))
        self.assertIn("positive MATCH 0.0% (runs=2)", share(0, 2))

    def test_class_letter_cross_check(self):
        bad = ["kind=same check_codes v=2 a=NEEDS_REVIEW n=1 codes=1MDD001W;n",    # M but class W
               "kind=same check_codes v=2 a=NEEDS_REVIEW n=1 codes=1NPP001C;n",    # N but class C
               "kind=same check_codes v=2 a=NEEDS_REVIEW n=1 codes=1NPP101W;n",    # fine but not F
               "kind=same check_codes v=2 a=NEEDS_REVIEW n=1 codes=1NPN001W;n",    # NONE but not V
               "kind=same check_codes v=2 a=NEEDS_REVIEW n=1 codes=1APP001W;n"]    # A but not O
        for text in bad:
            self.assertEqual(recompute.recompute([text])["inconsistent"], 1, text)
        good = ["check_codes v=2 a=NEEDS_REVIEW n=1 codes=1NPP101F;n", "check_codes v=2 a=NEEDS_REVIEW n=1 codes=1APD001O;n",
                "check_codes v=2 a=NEEDS_REVIEW n=1 codes=1VNN001V;n", "check_codes v=2 a=MISMATCH n=1 codes=1XDD001D;n"]
        for text in good:
            self.assertEqual(recompute.recompute([text])["inconsistent"], 0, text)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.txt")
            with open(p, "w") as f:
                f.write(bad[0])
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(recompute._main([p]), 1)
        self.assertIn("contradicts outcome/evidence/fine: 1", buf.getvalue())


class PrivacyTests(unittest.TestCase):
    def inventory(self):
        return [{"attribute_id": MARK + ".id%d" % i, "claim": MARK + " 7 label %d: два" % i,
                 "reference_roi": BIG} for i in range(2)]

    def test_claim_never_in_record_line_logs_or_recompute_output(self):
        inv = self.inventory()
        extra = {**EXTRA, "description": MARK + " pocket"}

        def mark(i, a):
            for r in a["results"]:
                r["observation"] = MARK + " seen"
        model = FakeModel(inventory=inv, per_order=((None, (extra,)),) * 2, mutate=mark)
        compare = vision_compare.compare_from_env(ENV, transport=model)
        with self.assertLogs("vision", "DEBUG") as logs:
            run_bot_pair(Session(), compare, None)
        lines = [x for x in logs.output if "check_codes " in x]
        self.assertEqual(len(lines), 1)
        self.assertIn("v=3", lines[0])
        self.assertNotIn(MARK, repr(logs.output))
        record, _ = record_of([(None, (extra,))] * 2, inv)
        self.assertNotIn(MARK, repr(record))
        self.assertNotIn(MARK, vision_core.codes_line(record))
        self.assertNotIn("id0", vision_core.codes_line(record))
        # the recompute prints counts only
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.txt")
            with open(p, "w") as f:
                f.write("\n".join(lines))
            buf = io.StringIO()
            with redirect_stdout(buf):
                recompute._main([p])
        self.assertNotIn(MARK, buf.getvalue())

    def test_flag_function_does_not_log_or_print(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertNoLogs(level=logging.DEBUG):
            vision_core.text_logo_count(MARK + " 7 два логотип")
        self.assertEqual(buf.getvalue(), "")

    def test_claim_not_kept_in_classified_rows_of_the_record(self):
        inv = self.inventory()
        record, _ = record_of([(None, ())] * 2, inv)
        for a in record["attrs"]:
            self.assertEqual(set(a), {"pos", "outcome", "evidence", "fine", "tlc", "both_match", "cls"})
            self.assertIsInstance(a["tlc"], bool)


if __name__ == "__main__":
    unittest.main()
