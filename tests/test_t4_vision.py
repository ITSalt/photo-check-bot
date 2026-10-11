"""T4 tests: fake transports only. No network, no keys.

Every chain test runs through the real T3b adapter
(core_t3.photo_compare_t3_candidate.call_provider); only the transport is faked.
"""
import json
import logging
import urllib.error
import unittest
from unittest.mock import patch

from app.photo_compare import ProviderUnavailable, run_bot_pair
from app import vision_compare, vision_controls, vision_core, vision_request, vision_transport
from app.verdict_notes import REASON_RU, format_notes
from app.vision_request import AnswerError, ImageRejected
from core_t2 import photo_compare_t2_candidate as t2
from core_t3 import photo_compare_t3_candidate as t3
from app.vision_transport import TransportConfigError, make_transport

def jpeg(w, h, tail=b""):
    """SOI + a baseline SOF0 header of w x h (what app/intake/sniff.py reads) + tail."""
    return (b"\xff\xd8\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big")
            + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01" + tail)


def png(w, h, tail=b""):
    return (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00" + tail)


def webp(w, h, tail=b""):
    return (b"RIFF\x10\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x00\x00\x00\x00"
            + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little") + tail)


# Telegram's largest photo variant is 1280 px on its longest side.
JPEG = jpeg(1280, 960, b"SECRETJPEGBYTES" * 4)
PNG = png(960, 1280, b"SECRETPNGBYTES" * 4)
WEBP = webp(1024, 1024, b"SECRETWEBPBYTES")
KEY = "sk-top-secret-key"
URL = "https://openrouter.ai/api/v1/chat/completions"
ENV = {"VISION_BASE_URL": URL, "VISION_MODEL": "google/gemini-3.6-flash",
       "VISION_HOST_ALLOWLIST": "openrouter.ai", "VISION_API_KEY": KEY}


class FakeResponse:
    def __init__(self, status=200, data=b"{}"):
        self.status, self.data, self.closed = status, data, False

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.deleted = 0

    def photos(self):
        return JPEG, PNG

    def delete_all(self):
        self.deleted += 1


FINE = {"attribute_id": "logo.text", "claim": "текст логотипа: BRAND", "reference_roi": [500, 600, 100, 100]}
# Three attributes: rule A' (WP-CONTROLS-04, D-10) gives MATCH only from 3 confirmed attributes on.
INV = [{"attribute_id": "button.count", "claim": "пуговицы: две", "reference_roi": [100, 100, 300, 400]},
       {"attribute_id": "print.layout", "claim": "принт: логотип по центру", "reference_roi": [500, 100, 300, 400]},
       {"attribute_id": "body.colour", "claim": "цвет ткани: синий", "reference_roi": [100, 550, 300, 400]}]



def chat(content, usage=None, finish="stop"):
    text = content if isinstance(content, str) else json.dumps(content)
    body = {"choices": [{"message": {"content": text}, "finish_reason": finish}]}
    if usage is not None:
        body["usage"] = usage
    return body


def result(attr, verdict="MATCH", evidence="DIRECT", view=None):
    """One compare result. `view` is candidate_view (WP-CONTROLS-04); None leaves the field
    out: that is the bare T2 shape, read as IN_VIEW by vision_core.parse_compare."""
    out = {"attribute_id": attr["attribute_id"], "verdict": verdict, "evidence_quality": evidence,
           "reference_roi": None if verdict == "NOT_VISIBLE_IN_REFERENCE" else attr["reference_roi"],
           "candidate_roi": [120, 90, 300, 400], "observation": "Same in both."}
    if view is not None:
        out["candidate_view"] = view
    return out


def compare_answer(sha, overrides=None, unmapped=(), attrs=INV):
    overrides = overrides or {}
    return {"schema_version": "photo-compare/2", "inventory_sha256": sha,
            "results": [result(a, *overrides.get(a["attribute_id"], ())) for a in attrs],
            "unmapped_candidate_elements": list(unmapped)}


def parts_of(request):
    return request["messages"][0]["content"]


class FakeModel:
    """A fake transport that plays the model: inventory call, then two compare calls.

    per_order[i] is (overrides, unmapped) for the i-th compare call; `mutate` may edit
    the answer dict; `fail_at` is a stage index (0 inventory, 1/2 compare) that returns
    the given reply instead. Like the real model under the WP-CONTROLS-04 prompt it writes
    candidate_view on every result (IN_VIEW unless an override says otherwise); `views=False`
    plays a model that leaves the field out.
    """
    def __init__(self, per_order=((None, ()), (None, ())), inventory=INV, usage=None,
                 fail_at=None, reply=None, mutate=None, views=True):
        self.per_order, self.inventory, self.usage = per_order, inventory, usage
        self.fail_at, self.reply, self.mutate = fail_at, reply, mutate
        self.views = views
        self.requests = []

    def __call__(self, request, headers, timeout):
        index = len(self.requests)
        self.requests.append(request)
        if self.fail_at == index:
            if isinstance(self.reply, BaseException):
                raise self.reply
            return self.reply
        parts = parts_of(request)
        if sum(p["type"] == "image_url" for p in parts) == 1:
            return {"status": 200, "body": chat({"attributes": self.inventory}, self.usage)}
        sha = next(p["text"].split(": ", 1)[1] for p in parts if p["text"].startswith("inventory_sha256: "))
        overrides, unmapped = self.per_order[index - 1]
        answer = compare_answer(sha, overrides, unmapped, attrs=self.inventory)
        if self.views:
            for item in answer["results"]:
                item.setdefault("candidate_view", "IN_VIEW")
        if self.mutate:
            self.mutate(index, answer)
        return {"status": 200, "body": chat(answer, self.usage)}


class RequestTests(unittest.TestCase):
    def test_inventory_request_shape(self):
        req = vision_request.build_inventory_request(JPEG, "m/x")
        self.assertEqual((req["model"], req["temperature"]), ("m/x", 0))
        self.assertEqual(req["response_format"], {"type": "json_object"})
        self.assertEqual(req["reasoning"], {"effort": "minimal"})
        self.assertEqual(req["provider"], {"data_collection": "deny"})
        self.assertEqual(req["usage"], {"include": True})
        images = [p for p in parts_of(req) if p["type"] == "image_url"]
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("do not invent", parts_of(req)[0]["text"])

    def test_compare_requests_swap_positions_not_roles(self):
        import base64
        validated = vision_core.parse_inventory(chat({"attributes": INV}))
        first, second = vision_core.build_compare_requests(validated, JPEG, PNG, "m/x")
        for req in (first, second):
            self.assertEqual((req["model"], req["temperature"]), ("m/x", 0))
            self.assertEqual(req["response_format"], {"type": "json_object"})
            self.assertEqual(req["reasoning"], {"effort": "minimal"})
            self.assertEqual(req["provider"], {"data_collection": "deny"})
            self.assertEqual(req["usage"], {"include": True})
            self.assertEqual(sum(p["type"] == "image_url" for p in parts_of(req)), 2)
            text = parts_of(req)[0]["text"]
            for word in ("shape", "colour", "logo", "proportions", "background", "lighting", "angle"):
                self.assertIn(word, text)

        def layout(req):
            out, label = [], None
            for p in parts_of(req):
                if p["type"] == "text" and p["text"].startswith("Image "):
                    label = p["text"]
                elif p["type"] == "image_url":
                    out.append((label.split("role=")[1], base64.b64decode(p["image_url"]["url"].split(",")[1])))
            return out
        self.assertEqual(layout(first), [("reference", JPEG), ("candidate", PNG)])
        self.assertEqual(layout(second), [("candidate", PNG), ("reference", JPEG)])
        def inv_of(req):
            return [p["text"] for p in parts_of(req) if p["type"] == "text" and p["text"].startswith("inventory")]
        inv = inv_of(first)
        self.assertEqual(inv, inv_of(second))
        self.assertIn("inventory_sha256: " + validated["sha256"], inv)

    def test_rejections(self):
        for bad, code in ((b"GIF89a....", "NOT_IMAGE"), (b"%PDF-1.4", "NOT_IMAGE"), (b"", "EMPTY"),
                          (None, "EMPTY"), ("text", "EMPTY"), (b"RIFF\0\0\0\0WAVE", "NOT_IMAGE")):
            with self.assertRaises(ImageRejected) as cm:
                vision_request.build_inventory_request(bad, "m")
            self.assertEqual(cm.exception.args[0], code)
        validated = vision_core.parse_inventory(chat({"attributes": INV}))
        for args in ((b"GIF89a", PNG), (JPEG, b"GIF89a")):
            with self.assertRaises(ImageRejected):
                vision_core.build_compare_requests(validated, *args, "m")

    def test_webp_accepted_and_not_reencoded(self):
        import base64
        req = vision_request.build_inventory_request(WEBP, "m")
        url = parts_of(req)[2]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/webp;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), WEBP)

    def test_size_cap_boundary(self):
        big = JPEG + b"\0" * 100
        vision_request.build_inventory_request(big, "m", max_image_bytes=len(big))
        with self.assertRaises(ImageRejected) as cm:
            vision_request.build_inventory_request(big, "m", max_image_bytes=len(big) - 1)
        self.assertEqual(cm.exception.args[0], "TOO_LARGE")
        validated = vision_core.parse_inventory(chat({"attributes": INV}))
        with self.assertRaises(ImageRejected):
            vision_core.build_compare_requests(validated, JPEG, big, "m", max_image_bytes=len(big) - 1)
        self.assertEqual(vision_request.MAX_IMAGE_BYTES, 8 * 1024 * 1024)

    def test_error_text_has_no_bytes(self):
        try:
            vision_request.build_inventory_request(b"GIF89a" + b"PRIVATE", "m")
        except ImageRejected as exc:
            self.assertNotIn("PRIVATE", repr(exc))

    def test_model_required(self):
        for m in ("", "  ", None):
            with self.assertRaises(ValueError):
                vision_request.build_inventory_request(JPEG, m)


class CoreTests(unittest.TestCase):
    def test_inventory_good(self):
        v = vision_core.parse_inventory(chat({"attributes": INV}))
        self.assertEqual(v["attribute_ids"], ["button.count", "print.layout", "body.colour"])
        self.assertEqual(v["reference_photo_id"], "reference")
        self.assertEqual(v["sha256"], t2.sha256_hex(v["inventory"]))

    def test_inventory_bad(self):
        a = dict(INV[0])
        cases = [chat("nope"), {}, chat({"attributes": []}), chat({"attributes": "x"}), chat({"attributes": INV, "x": 1}),
                 chat({"attributes": [a, a]}), chat({"attributes": [{**a, "reference_roi": [1000, 100, 300, 400]}]}),
                 chat({"attributes": [{**a, "confidence": 1}]}), chat({"attributes": [{**a, "claim": ""}]}),
                 chat({"attributes": [{**INV[0], "attribute_id": "a%d" % i} for i in range(13)]}),
                 chat({"attributes": INV}, finish="length"), chat([1])]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(AnswerError):
                vision_core.parse_inventory(body)
        ok = [{**INV[0], "attribute_id": "a%d" % i} for i in range(12)]
        vision_core.parse_inventory(chat({"attributes": ok}))

    def test_compare_validated_by_t2(self):
        v = vision_core.parse_inventory(chat({"attributes": INV}))
        good = compare_answer(v["sha256"])
        # exactly what T2 validated, plus the side map of candidate views (WP-CONTROLS-04)
        views = {a["attribute_id"]: "IN_VIEW" for a in INV}
        self.assertEqual(vision_core.parse_compare(chat(good), v), {**good, "candidate_view": views})
        bad = []
        x = compare_answer(v["sha256"]); x["inventory_sha256"] = "0" * 64; bad.append(x)
        x = compare_answer(v["sha256"]); x["results"].pop(); bad.append(x)
        x = compare_answer(v["sha256"]); x["results"][0]["confidence"] = 1; bad.append(x)
        x = compare_answer(v["sha256"], {"button.count": ("MATCH", "NONE")}); bad.append(x)
        x = compare_answer(v["sha256"]); x["results"][0]["attribute_id"] = "invented"; bad.append(x)
        x = compare_answer(v["sha256"]); x["schema_version"] = "photo-compare/1"; bad.append(x)
        x = compare_answer(v["sha256"]); x["results"][0]["observation"] = "x" * 161; bad.append(x)
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(AnswerError):
                vision_core.parse_compare(chat(payload), v)


def run(per_order, inventory=INV):
    """assess() on two scripted compare answers."""
    v = vision_core.parse_inventory(chat({"attributes": inventory}))
    sha = v["sha256"]
    answers = [compare_answer(sha, o, u, attrs=inventory) for o, u in per_order]
    return vision_core.assess(v, *[vision_core.parse_compare(chat(a), v) for a in answers])


def reasons(result):
    return {i["attribute_id"]: i["reason"] for i in result["to_check"]}


class AssessTests(unittest.TestCase):
    def test_all_confirmed_is_match(self):
        r = run([(None, ()), (None, ())])
        self.assertEqual(r, {"status": "MATCH", "total": 3,
                             "confirmed": ["button.count", "print.layout", "body.colour"],
                             "to_check": [], "differs": [], "extra": "none", "not_compared": []})

    def test_prompts_exclude_scene_and_ask_for_product_only(self):
        inv = vision_request.INVENTORY_INSTRUCTION
        for word in ("background", "scene", "lighting", "angle", "props", "hands", "people"):
            self.assertIn(word, inv)
        self.assertIn("NEVER list", inv)
        self.assertIn("ONLY parts of the product itself", vision_core.COMPARE_INSTRUCTION)
        self.assertIn("NEVER list the background", vision_core.COMPARE_INSTRUCTION)
        self.assertIn("MISMATCH with evidence_quality NONE", vision_core.COMPARE_INSTRUCTION)
        for word in ("candidate_view", "IN_VIEW", "OUT_OF_VIEW", "inner side", "cropped out"):
            self.assertIn(word, vision_core.COMPARE_INSTRUCTION)

    def test_to_check_reasons(self):
        weak = ("MATCH", "PARTIAL")
        r = run([({"button.count": weak}, ()), ({"button.count": weak}, ())])
        self.assertEqual((r["status"], reasons(r), r["confirmed"]), ("NEEDS_REVIEW", {"button.count": "weak_evidence"}, ["print.layout", "body.colour"]))
        nv = ("NOT_VISIBLE_IN_REFERENCE", "NONE")
        r = run([({"button.count": nv}, ())] * 2)
        self.assertEqual(reasons(r), {"button.count": "not_visible"})
        hidden = ("MISMATCH", "NONE")  # candidate angle hides it: NOT a mismatch
        r = run([({"button.count": hidden}, ())] * 2)
        self.assertEqual((r["status"], reasons(r), r["differs"]), ("NEEDS_REVIEW", {"button.count": "not_visible"}, []))
        r = run([(None, ()), ({"button.count": ("MISMATCH", "DIRECT")}, ())])
        self.assertEqual((r["status"], reasons(r)), ("NEEDS_REVIEW", {"button.count": "orders_disagree"}))

    def test_hidden_in_one_order_only(self):
        r = run([({"button.count": ("MISMATCH", "PARTIAL")}, ()), ({"button.count": ("MISMATCH", "NONE")}, ())])
        self.assertEqual(reasons(r), {"button.count": "not_visible"})
        r = run([({"button.count": ("MISMATCH", "NONE")}, ()), ({"button.count": ("MISMATCH", "PARTIAL")}, ())])
        self.assertEqual(reasons(r), {"button.count": "not_visible"})

    def test_absent_where_visible_is_mismatch(self):
        both = ({"print.layout": ("MISMATCH", "DIRECT")}, ())
        r = run([both, both])
        self.assertEqual(r["status"], "MISMATCH")
        self.assertEqual(r["differs"], [{"attribute_id": "print.layout", "label": "принт"}])
        self.assertEqual(r["confirmed"], ["button.count", "body.colour"])

    def test_fine_detail_rules(self):
        inv = INV + [FINE]
        self.assertEqual(run([(None, ())] * 2, inv)["status"], "MATCH")  # direct in both orders: confirmed
        weak = ({"logo.text": ("MATCH", "PARTIAL")}, ())
        r = run([weak, weak], inv)
        self.assertEqual((r["status"], reasons(r)), ("NEEDS_REVIEW", {"logo.text": "fine_detail_unverified"}))
        r = run([(None, ()), ({"logo.text": ("MISMATCH", "DIRECT")}, ())], inv)
        self.assertEqual(reasons(r), {"logo.text": "fine_detail_unverified"})
        # the same weak evidence on a coarse attribute is labelled differently, same status
        r = run([({"button.count": ("MATCH", "PARTIAL")}, ())] * 2, inv)
        self.assertEqual(reasons(r), {"button.count": "weak_evidence"})
        # hidden fine detail is "not visible", the more useful note
        hid = ({"logo.text": ("MISMATCH", "NONE")}, ())
        self.assertEqual(reasons(run([hid, hid], inv)), {"logo.text": "not_visible"})

    def test_fine_threshold_boundary(self):
        self.assertTrue(vision_core.is_fine([0, 0, 100, 199]))
        self.assertFalse(vision_core.is_fine([0, 0, 100, 200]))

    def test_extra_elements(self):
        r = run([(None, (EXTRA,)), (None, (EXTRA,))])
        self.assertEqual((r["status"], r["extra"]), ("MISMATCH", "confirmed"))
        r = run([(None, (EXTRA,)), (None, ())])
        self.assertEqual((r["status"], r["extra"], r["to_check"]), ("NEEDS_REVIEW", "unconfirmed", []))

    def test_same_product_on_two_backgrounds_is_not_mismatch(self):
        # A correct model reports no scene differences: nothing for code to turn into MISMATCH.
        for candidate in (PNG, PNG + b"other-background"):
            model = FakeModel()
            compare = vision_compare.compare_from_env(ENV, transport=model)
            self.assertEqual(compare(JPEG, candidate)["status"], "MATCH")

    def test_known_gap_scene_extra_is_prompt_only(self):
        # T2 unmapped elements have no product/scene field, so code cannot tell them apart:
        # a model that lists the table in both orders produces MISMATCH. Only the prompt prevents it.
        scene = {"candidate_roi": BOX, "description": "table", "visibility_in_reference": "VISIBLE_AND_ABSENT"}
        self.assertEqual(run([(None, (scene,))] * 2)["status"], "MISMATCH")

    def test_labels(self):
        label = vision_core.label_of
        self.assertEqual(label("застёжка: молния"), "застёжка")
        self.assertEqual(label("текст логотипа"), "текст логотипа")
        self.assertEqual(label("see evil.example/x @bot\nnow: y"), "see evil example x bot now")
        self.assertNotIn(".", label("evil.com: x"))
        self.assertEqual(len(label("я" * 100)), 40)
        self.assertEqual(label(":: x"), "деталь")

    def test_review_share_arithmetic(self):
        f = vision_core.expected_review_share
        self.assertAlmostEqual(f(12, 0.98), 1 - 0.98 ** 12)
        self.assertAlmostEqual(f(12, 0.95), 0.4596, places=3)
        self.assertAlmostEqual(f(1, 1.0), 0.0)
        self.assertAlmostEqual(f(6, 0.95, 0.1), 1 - 0.95 ** 6 * 0.9)


class NotesTests(unittest.TestCase):
    def result(self, **kw):
        base = {"status": "NEEDS_REVIEW", "total": 12, "confirmed": ["a"] * 9, "differs": [], "extra": "none",
                "to_check": [{"attribute_id": "c", "label": "застёжка", "reason": "not_visible"},
                             {"attribute_id": "l", "label": "текст логотипа", "reason": "fine_detail_unverified"},
                             {"attribute_id": "w", "label": "цвет", "reason": "weak_evidence"},
                             {"attribute_id": "o", "label": "карман", "reason": "orders_disagree"}][:3]}
        base.update(kw)
        return base

    def test_example_text(self):
        r = self.result()
        r["to_check"] = r["to_check"][:2]
        self.assertEqual(format_notes(r), "Совпадает: 9 из 12. Проверьте сами: застёжка (не видно), текст логотипа (мелкая деталь).")

    def test_all_reasons_words(self):
        self.assertEqual(set(REASON_RU), {"not_visible", "orders_disagree", "weak_evidence", "fine_detail_unverified"})
        text = format_notes(self.result(to_check=self.result()["to_check"] + [
            {"attribute_id": "o", "label": "карман", "reason": "orders_disagree"}]))
        for word in ("не видно", "мелкая деталь", "не уверены", "ответы расходятся"):
            self.assertIn(word, text)

    def test_mismatch_and_extras(self):
        text = format_notes(self.result(status="MISMATCH", to_check=[], differs=[{"attribute_id": "p", "label": "принт"}],
                                        extra="confirmed"))
        self.assertEqual(text, "Совпадает: 9 из 12. Отличается: принт. На сгенерированном фото есть лишняя деталь товара.")
        self.assertIn("Возможна лишняя деталь", format_notes(self.result(to_check=[], extra="unconfirmed")))

    def test_list_cap_match_and_malformed(self):
        many = [{"attribute_id": str(i), "label": "д%d" % i, "reason": "weak_evidence"} for i in range(9)]
        text = format_notes(self.result(to_check=many))
        self.assertIn("д5 (не уверены) и ещё 3", text)
        self.assertNotIn("д6", text)
        self.assertEqual(format_notes(self.result(status="MATCH", to_check=[])), "")
        for bad in (None, {}, {"status": "MATCH"}, self.result(status="UNAVAILABLE"), self.result(total="x"),
                    self.result(to_check=[{"label": "x", "reason": "weird"}])):
            self.assertEqual(format_notes(bad), "")

    def test_photo_checks_appends_notes_and_keeps_t3a_result(self):
        from app.photo_checks import MESSAGES, PhotoChecks
        detail = self.result()
        detail["to_check"] = detail["to_check"][:1]
        for compare_result, expected in (
                (detail, MESSAGES["NEEDS_REVIEW"] + " Совпадает: 9 из 12. Проверьте сами: застёжка (не видно)."),
                ({"status": "NEEDS_REVIEW"}, MESSAGES["NEEDS_REVIEW"]),
                ({**detail, "status": "MATCH"}, MESSAGES["MATCH"]),
                ({"status": "UNAVAILABLE"}, MESSAGES["UNAVAILABLE"])):
            session = Session()
            checks = PhotoChecks(lambda a, b: session, lambda r, c: compare_result)
            checks.handle({"user_id": 1, "chat_id": 1, "kind": "photo", "file_id": "g", "caption": None})
            self.assertEqual(checks.handle({"user_id": 1, "chat_id": 1, "kind": "photo", "file_id": "r", "caption": "real"}), [expected])
        self.assertEqual(run_bot_pair(Session(), lambda r, c: detail, None),
                         {"schema_version": "photo-bot-result/1", "status": "NEEDS_REVIEW"})


class ControlsTests(unittest.TestCase):
    def fake(self, table):
        calls = []

        def compare(ref, cand):
            calls.append((ref, cand))
            return {"status": table[(ref == cand)].pop(0)}
        return compare, calls

    def test_run_and_summary(self):
        files = {"a": b"A", "b": b"B", "c": b"C"}
        rows = [{"kind": "same", "ref": "a"}, {"kind": "recrop", "ref": "a", "cand": "b"},
                {"kind": "different", "ref": "a", "cand": "c"}]
        compare, calls = self.fake({True: ["MATCH", "MATCH"], False: ["MATCH", "NEEDS_REVIEW", "MISMATCH", "MISMATCH", "UNAVAILABLE", "x"]})
        tally = vision_controls.run_controls(rows, compare, 2, files.__getitem__)
        self.assertEqual(calls[0], (b"A", b"A"))
        self.assertEqual(tally["same"]["MATCH"], 2)
        self.assertEqual(tally["recrop"], {"MATCH": 1, "MISMATCH": 0, "NEEDS_REVIEW": 1, "UNAVAILABLE": 0})
        self.assertEqual(tally["different"]["MISMATCH"], 2)
        out = vision_controls.summarize(tally)
        self.assertEqual(out["positive"]["runs"], 4)
        self.assertEqual(out["positive"]["MATCH"], 0.75)
        self.assertEqual(out["positive"]["NEEDS_REVIEW"], 0.25)
        self.assertTrue(out["rule_too_strict"])

    def test_threshold_boundary(self):
        def tally(m, n):
            t = {k: {s: 0 for s in vision_controls.STATUSES} for k in vision_controls.KINDS}
            t["same"]["MATCH"], t["same"]["NEEDS_REVIEW"] = m, n
            return t
        self.assertFalse(vision_controls.summarize(tally(9, 1))["rule_too_strict"])
        self.assertTrue(vision_controls.summarize(tally(89, 11))["rule_too_strict"])

    def test_unknown_kind_and_main(self):
        with self.assertRaises(ValueError):
            vision_controls.run_controls([{"kind": "x", "ref": "a"}], lambda a, b: {}, 1, lambda p: b"")
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, "m.json")
            open(m, "w").write(json.dumps([{"kind": "same", "ref": "a"}, {"kind": "different", "ref": "a", "cand": "b"}]))
            read = lambda p: p.encode()
            self.assertEqual(vision_controls._main([m, "--repeats", "2"], {}, lambda a, b: {"status": "MATCH" if a == b else "MISMATCH"}, read), 0)
            self.assertEqual(vision_controls._main([m], {}, lambda a, b: {"status": "NEEDS_REVIEW"}, read), 1)
        self.assertEqual(vision_controls._main([], {}), 2)
        self.assertEqual(vision_controls._main(["m.json"], {}), 2)

    def test_output_has_no_paths(self):
        import io, os, tempfile
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, "secretname.json")
            open(m, "w").write(json.dumps([{"kind": "same", "ref": "private-file-name"}]))
            buf = io.StringIO()
            with redirect_stdout(buf):
                vision_controls._main([m], {}, lambda a, b: {"status": "MATCH"}, lambda p: b"x")
        self.assertNotIn("private-file-name", buf.getvalue())
        self.assertNotIn("secretname", buf.getvalue())


class TransportTests(unittest.TestCase):
    def make(self, resp=None, exc=None, **kw):
        seen = []

        def opener(req, timeout):
            seen.append((req, timeout))
            if exc:
                raise exc
            return resp
        kw.setdefault("host_allowlist", {"openrouter.ai"})
        return make_transport(URL, 60, opener=opener, **kw), seen

    def test_success_and_request_building(self):
        resp = FakeResponse(200, b'{"a": 1}')
        t, seen = self.make(resp)
        out = t({"model": "m"}, {"Authorization": "Bearer k"}, 20)
        self.assertEqual(out, {"status": 200, "body": {"a": 1}})
        req, timeout = seen[0]
        self.assertEqual(timeout, 20.0)
        self.assertEqual(req.full_url, URL)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"model": "m"})
        self.assertEqual(req.get_header("Authorization"), "Bearer k")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertTrue(resp.closed)

    def test_timeout_capped(self):
        t, seen = self.make(FakeResponse())
        t({}, {}, 500)
        self.assertEqual(seen[0][1], 60.0)

    def test_bad_call_timeout(self):
        t, _ = self.make(FakeResponse())
        for bad in (0, -1, None, "5", True):
            with self.assertRaises(TransportConfigError):
                t({}, {}, bad)

    def test_host_allowlist_and_url_rules(self):
        bad_urls = ["http://openrouter.ai/x", "ftp://openrouter.ai/", "https://evil.example/x",
                    "https://openrouter.ai.evil.example/x", "https://user:pw@openrouter.ai/x",
                    "https://openrouter.ai:8443/x", "https://openrouter.ai:99999/x", "", None,
                    "//openrouter.ai/x", "https://openrouter.ai/x#f", "https:///x"]
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(TransportConfigError):
                make_transport(url, 30, host_allowlist={"openrouter.ai"})
        make_transport("https://OpenRouter.ai:443/x", 30, host_allowlist={"openrouter.ai"})
        with self.assertRaises(TransportConfigError):
            make_transport(URL, 30, host_allowlist=set())

    def test_config_messages_hide_url(self):
        try:
            make_transport("https://user:" + KEY + "@evil.example/", 30, host_allowlist={"openrouter.ai"})
        except TransportConfigError as exc:
            self.assertNotIn(KEY, repr(exc))

    def test_parse_allowlist(self):
        self.assertEqual(vision_transport.parse_allowlist(" A.com , b.org,"), frozenset({"a.com", "b.org"}))
        for bad in ("", " , ", None, "a.com/x", "a b", "-a.com"):
            with self.assertRaises(TransportConfigError):
                vision_transport.parse_allowlist(bad)

    def test_timeout_caps_validated(self):
        for bad in (0, -1, 121, None, True, "30"):
            with self.assertRaises(TransportConfigError):
                make_transport(URL, bad, host_allowlist={"openrouter.ai"})
        make_transport(URL, 120, host_allowlist={"openrouter.ai"})

    def test_redirect_refused_not_followed(self):
        for code in (301, 302, 303, 307, 308):
            resp = FakeResponse(code, b'{"choices": []}')
            t, seen = self.make(resp)
            self.assertEqual(t({}, {"Authorization": "Bearer k"}, 5), {"status": code, "body": None})
            self.assertEqual(len(seen), 1)  # one request, no second hop

    def test_real_opener_has_no_redirect_http_or_proxy_handlers(self):
        import urllib.request as ur
        handlers = vision_transport._default_opener().handlers
        self.assertFalse(any(isinstance(h, ur.HTTPRedirectHandler) for h in handlers))
        self.assertFalse(any(type(h) is ur.HTTPHandler for h in handlers))
        self.assertTrue(any(isinstance(h, ur.HTTPSHandler) for h in handlers))
        self.assertFalse(any(isinstance(h, ur.ProxyHandler) for h in handlers))

    def test_non_200_has_no_body(self):
        for code in (400, 401, 429, 500, 503):
            t, _ = self.make(FakeResponse(code, b'{"error": "KEY-ECHO"}'))
            self.assertEqual(t({}, {}, 5), {"status": code, "body": None})

    def test_response_cap(self):
        data = b'{"a": "' + b"x" * 100 + b'"}'
        t, _ = self.make(FakeResponse(200, data), response_cap=len(data))
        self.assertIsInstance(t({}, {}, 5)["body"], dict)
        t, _ = self.make(FakeResponse(200, data), response_cap=len(data) - 1)
        self.assertEqual(t({}, {}, 5), {"status": 200, "body": None})

    def test_malformed_bodies(self):
        for data in (b"not json", b"\xff\xfe", b"[1]", b'"x"', b"null", b""):
            t, _ = self.make(FakeResponse(200, data))
            self.assertEqual(t({}, {}, 5), {"status": 200, "body": None})

    def test_timeouts_pass_through(self):
        import socket
        for exc in (TimeoutError("x"), socket.timeout("x"), urllib.error.URLError(TimeoutError("x"))):
            t, _ = self.make(exc=exc)
            with self.assertRaises(TimeoutError):
                t({}, {}, 5)

    def test_network_errors_become_status(self):
        for exc in (urllib.error.URLError("dns " + KEY), ConnectionResetError(KEY), OSError(KEY)):
            t, _ = self.make(exc=exc)
            self.assertEqual(t({}, {}, 5), {"status": vision_transport.STATUS_NETWORK_ERROR, "body": None})


class ConfigTests(unittest.TestCase):
    def test_valid(self):
        cfg = vision_compare.load_config(ENV)
        self.assertEqual(cfg["model"], "google/gemini-3.6-flash")
        self.assertEqual(cfg["timeout"], vision_compare.DEFAULT_TIMEOUT)
        self.assertEqual(vision_compare.load_config({**ENV, "VISION_TIMEOUT": "12.5"})["timeout"], 12.5)

    def test_missing_keeps_old_path(self):
        self.assertIsNone(vision_compare.load_config({}))
        self.assertIsNone(vision_compare.compare_from_env({}))
        for k in ("VISION_BASE_URL", "VISION_MODEL", "VISION_HOST_ALLOWLIST", "VISION_API_KEY"):
            env = {a: b for a, b in ENV.items() if a != k}
            self.assertIsNone(vision_compare.compare_from_env(env), k)
            self.assertIsNone(vision_compare.compare_from_env({**ENV, k: "  "}), k)

    def test_partial_warning_names_no_values(self):
        with self.assertLogs("vision", "WARNING") as logs:
            vision_compare.load_config({k: v for k, v in ENV.items() if k != "VISION_MODEL"})
        text = repr(logs.output)
        self.assertIn("VISION_MODEL", text)
        self.assertNotIn(KEY, text)
        self.assertNotIn("openrouter", text)

    def test_invalid_raises(self):
        for patch_ in ({"VISION_MODEL": "bad model!"}, {"VISION_MODEL": "x" * 101}, {"VISION_TIMEOUT": "abc"},
                       {"VISION_TIMEOUT": "0"}, {"VISION_TIMEOUT": "-3"}, {"VISION_TIMEOUT": "121"},
                       {"VISION_TIMEOUT": "nan"}, {"VISION_BASE_URL": "http://openrouter.ai/x"},
                       {"VISION_BASE_URL": "https://evil.example/x"}, {"VISION_HOST_ALLOWLIST": "a b"}):
            with self.subTest(patch_=patch_), self.assertRaises(vision_compare.VisionConfigError):
                vision_compare.load_config({**ENV, **patch_})

    def test_no_t3b_available_disables(self):
        with patch.object(vision_compare, "_default_call_provider", return_value=None):
            self.assertIsNone(vision_compare.compare_from_env(ENV))

    def test_default_is_the_real_t3b_adapter(self):
        self.assertIs(vision_compare._default_call_provider(), t3.call_provider)
        compare = vision_compare.compare_from_env(ENV, transport=FakeModel())
        session = Session()
        self.assertEqual(run_bot_pair(session, compare, None),
                         {"schema_version": "photo-bot-result/1", "status": "MATCH"})
        self.assertEqual(session.deleted, 1)

    def test_no_injectable_call_provider(self):
        with self.assertRaises(TypeError):
            vision_compare.compare_from_env(ENV, call_provider=lambda *a: {})

    def test_bot_default_unchanged_without_config(self):
        import importlib, os
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "unit-test-placeholder"}):
            bot = importlib.import_module("app.bot")
        update = {"message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1},
                              "photo": [{"file_id": "x", "file_size": 10}]}}
        self.assertIsNone(vision_compare.compare_from_env({}))
        with patch.object(bot, "call") as send:
            bot.handle(update)
        self.assertEqual(send.call_args.args[1]["text"], bot.TEXTS["ru"]["fallback"])


BOX = [300, 300, 200, 200]
EXTRA = {"candidate_roi": BOX, "description": "extra pocket", "visibility_in_reference": "VISIBLE_AND_ABSENT"}


class ChainTests(unittest.TestCase):
    def run_chain(self, model, env=ENV):
        compare = vision_compare.compare_from_env(env, transport=model)
        session = Session()
        result = run_bot_pair(session, compare, None)
        self.assertEqual(session.deleted, 1)
        return result["status"]

    def test_consistent_match(self):
        model = FakeModel()
        self.assertEqual(self.run_chain(model), "MATCH")
        self.assertEqual(len(model.requests), 3)  # inventory + two orders
        self.assertTrue(all(r["model"] == "google/gemini-3.6-flash" for r in model.requests))

    def test_consistent_mismatch(self):
        both = ({"print.layout": ("MISMATCH", "DIRECT")}, ())
        self.assertEqual(self.run_chain(FakeModel(per_order=(both, both))), "MISMATCH")

    def test_order_swap_disagreement_needs_review(self):
        a = ({"print.layout": ("MISMATCH", "DIRECT")}, ())
        self.assertEqual(self.run_chain(FakeModel(per_order=((None, ()), a))), "NEEDS_REVIEW")
        self.assertEqual(self.run_chain(FakeModel(per_order=(a, (None, ())))), "NEEDS_REVIEW")

    def test_weak_evidence_and_not_visible_need_review(self):
        for state in (("MATCH", "PARTIAL"), ("NOT_VISIBLE_IN_REFERENCE", "PARTIAL")):
            o = ({"button.count": state}, ())
            self.assertEqual(self.run_chain(FakeModel(per_order=(o, o))), "NEEDS_REVIEW")

    def test_unmapped_element_coverage(self):
        both = (None, (EXTRA,))
        self.assertEqual(self.run_chain(FakeModel(per_order=(both, both))), "MISMATCH")
        self.assertEqual(self.run_chain(FakeModel(per_order=(both, (None, ())))), "NEEDS_REVIEW")
        occluded = (None, ({**EXTRA, "visibility_in_reference": "OCCLUDED_IN_REFERENCE"},))
        self.assertEqual(self.run_chain(FakeModel(per_order=(occluded, occluded))), "NEEDS_REVIEW")

    def test_malformed_answers_are_unavailable(self):
        def drop_result(i, a): a["results"].pop()
        def bad_sha(i, a): a["inventory_sha256"] = "f" * 64
        def extra(i, a): a["results"][0]["confidence"] = 1
        def second_only(i, a):
            if i == 2: a["results"][0]["evidence_quality"] = "WRONG"
        for mutate in (drop_result, bad_sha, extra, second_only):
            with self.subTest(mutate=mutate.__name__):
                self.assertEqual(self.run_chain(FakeModel(mutate=mutate)), "UNAVAILABLE")
        for inv in ([], [{"attribute_id": "a"}], "x"):
            self.assertEqual(self.run_chain(FakeModel(inventory=inv)), "UNAVAILABLE")

    def test_every_call_goes_through_core_t3_call_provider(self):
        model = FakeModel()
        with patch.object(t3, "call_provider", wraps=t3.call_provider) as spy:
            self.assertEqual(self.run_chain(model), "MATCH")
        self.assertEqual(spy.call_count, 3)
        self.assertEqual([c.args[0] for c in spy.call_args_list], model.requests)
        for c in spy.call_args_list:
            self.assertEqual(len(c.args), 4)
            self.assertIs(c.args[2], ENV)

    def test_provider_failure_at_any_stage_is_unavailable_and_final(self):
        for stage in (0, 1, 2):
            for reply, code in (({"status": 503, "body": None}, "HTTP_ERROR"),
                                ({"status": 200, "body": None}, "MALFORMED_BODY"),
                                (TimeoutError(KEY), "TIMEOUT"), (ConnectionError(KEY), "HTTP_ERROR")):
                raised = []

                def recording(*args, _real=t3.call_provider):
                    try:
                        return _real(*args)
                    except ProviderUnavailable as exc:
                        raised.append(exc)
                        raise
                model = FakeModel(fail_at=stage, reply=reply)
                with self.subTest(stage=stage, code=code), \
                        patch.object(t3, "call_provider", side_effect=recording) as spy, \
                        self.assertLogs("vision", "WARNING") as logs:
                    self.assertEqual(self.run_chain(model), "UNAVAILABLE")
                    self.assertEqual(spy.call_count, stage + 1)
                    self.assertEqual(len(model.requests), stage + 1)
                    self.assertEqual([str(e) for e in raised], [code])
                    stage_name = ("inventory", "compare_a", "compare_b")[stage]
                    self.assertIn("vision_unavailable stage=%s code=%s" % (stage_name, code),
                                  logs.output[-1])

    def test_outgoing_bodies_deny_data_collection_and_ask_usage(self):
        model = FakeModel(usage={"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0001})
        sent = []

        def opener(req, timeout):
            body = json.loads(req.data.decode("utf-8"))
            sent.append(body)
            reply = model(body, dict(req.header_items()), timeout)
            return FakeResponse(reply["status"], json.dumps(reply["body"]).encode("utf-8"))
        transport = make_transport(URL, 30, host_allowlist={"openrouter.ai"}, opener=opener)
        self.assertEqual(self.run_chain(transport), "MATCH")
        self.assertEqual(len(sent), 3)  # inventory, compare_a, compare_b
        for body in sent:
            self.assertEqual(body["provider"], {"data_collection": "deny"})
            self.assertEqual(body["usage"], {"include": True})

    def test_failures_at_each_stage_stop_the_chain(self):
        for stage in (0, 1, 2):
            for reply in ({"status": 503, "body": None}, {"status": 200, "body": None}, {"status": 302, "body": {}},
                          {"status": 200, "body": {"choices": []}}, TimeoutError(KEY)):
                model = FakeModel(fail_at=stage, reply=reply)
                with self.subTest(stage=stage, reply=reply):
                    self.assertEqual(self.run_chain(model), "UNAVAILABLE")
                    self.assertEqual(len(model.requests), stage + 1)  # nothing is asked after a failure

    def test_compare_itself_never_raises_and_logs_stage_and_code(self):
        model = FakeModel(mutate=lambda i, a: a["results"].pop())
        compare = vision_compare.compare_from_env(ENV, transport=model)
        with self.assertLogs("vision", "WARNING") as logs:
            self.assertEqual(compare(JPEG, PNG), {"status": "UNAVAILABLE"})
        self.assertEqual(logs.output, ["WARNING:vision:vision_bad_answer stage=compare_a code=BAD_COMPARE rule=RESULTS_MISSING path=results"])

    def test_non_image_never_reaches_transport(self):
        model = FakeModel()
        compare = vision_compare.compare_from_env(ENV, transport=model)
        self.assertEqual(compare(b"%PDF-1.4 private", PNG)["status"], "UNAVAILABLE")
        self.assertEqual(compare(JPEG, b"%PDF-1.4 private")["status"], "UNAVAILABLE")
        self.assertEqual(model.requests, [])

    def test_missing_key_fails_before_transport(self):
        model = FakeModel()
        config = vision_compare.load_config(ENV)
        compare = vision_compare.make_compare(config, {}, t3.call_provider, vision_core, model)
        self.assertEqual(compare(JPEG, PNG)["status"], "UNAVAILABLE")
        self.assertEqual(model.requests, [])

    def test_headers_carry_key(self):
        seen = []
        model = FakeModel()

        def spy(request, headers, timeout):
            seen.append(headers)
            return model(request, headers, timeout)
        self.run_chain(spy)
        self.assertEqual([h["Authorization"] for h in seen], ["Bearer " + KEY] * 3)

    def test_usage_logged_per_call(self):
        model = FakeModel(usage={"prompt_tokens": 1200, "completion_tokens": 30, "cost": 0.0004})
        with self.assertLogs("vision", "INFO") as logs:
            self.assertEqual(self.run_chain(model), "MATCH")
        lines = [l for l in logs.output if "vision_call" in l]
        self.assertEqual(len(lines), 3)
        for line, stage in zip(lines, ("inventory", "compare_a", "compare_b")):
            self.assertIn("vision_call model=google/gemini-3.6-flash usage_prompt=1200 usage_completion=30 "
                          "cost=0.0004 stage=" + stage, line)

    def test_usage_absent_or_bad(self):
        for usage in (None, "x", {}, {"prompt_tokens": "9", "completion_tokens": True, "cost": float("nan")}):
            with self.assertLogs("vision", "INFO") as logs:
                self.run_chain(FakeModel(usage=usage))
            calls = [line for line in logs.output if "vision_call" in line]
            self.assertEqual((len(calls), len(logs.output)), (3, 4))   # + one check_codes line
            for line in calls:
                self.assertIn("usage_prompt=n/a usage_completion=n/a cost=n/a", line)

    def test_logs_hold_no_secret_bytes_or_model_text(self):
        import base64
        secret_inv = [{**INV[0], "claim": "PRIVATE-CLAIM-TEXT"}, INV[1]]
        scenarios = [FakeModel(inventory=secret_inv, usage={"prompt_tokens": 1}),
                     FakeModel(fail_at=1, reply={"status": 500, "body": None}),
                     FakeModel(fail_at=2, reply=TimeoutError(KEY)),
                     FakeModel(mutate=lambda i, a: a["results"].pop())]
        for model in scenarios:
            with self.assertLogs("vision", "DEBUG") as logs:
                logging.getLogger("vision").info("sentinel")
                self.run_chain(model)
                self.run_chain(FakeModel(fail_at=0, reply={"status": 200, "body": {"choices": [{"message": {"content": KEY}}]}}))
                vision_compare.compare_from_env(ENV, transport=model)(b"%PDF " + PNG, PNG)
            text = repr(logs.output)
            for secret in (KEY, "Authorization", "Bearer", "SECRETJPEGBYTES", "SECRETPNGBYTES", "PRIVATE-CLAIM-TEXT",
                           base64.b64encode(JPEG).decode(), base64.b64encode(JPEG).decode()[:16]):
                self.assertNotIn(secret, text)

    def test_provider_sink_filters_events(self):
        with self.assertLogs("vision", "INFO") as logs:
            vision_compare._provider_sink("TIMEOUT")
            vision_compare._provider_sink("Bearer " + KEY)
            vision_compare._provider_sink({"k": KEY})
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn(KEY, repr(logs.output))

    def test_core_missing_disables(self):
        with patch.object(vision_compare, "_default_core", return_value=None):
            self.assertIsNone(vision_compare.compare_from_env(ENV))

    def test_cost_estimate(self):
        est = vision_compare.estimate_check_cost(0.30, 2.50)
        self.assertEqual(est["prompt_tokens"], 1100 + 300 + 2 * (2200 + 900))
        self.assertEqual(est["completion_tokens"], 400 + 1800)
        self.assertAlmostEqual(est["usd"], (7600 * 0.30 + 2200 * 2.50) / 1e6)

    def test_smoke_main_without_config(self):
        self.assertEqual(vision_compare._main(["a", "b"], {}), 2)
        self.assertEqual(vision_compare._main([], ENV), 2)


if __name__ == "__main__":
    unittest.main()
