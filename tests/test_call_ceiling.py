"""WP-BOT-12 criterion 4: a per-call cost ceiling (gpb-agent-7a28a9f720, zenith-claude,
board ae90ed2b). Fakes only: no network, no keys.

a. the pricing basis is pinned to the deployed model;
b. image input is bounded by the header size (no re-encoding), text by its byte length;
c. max_tokens is in every payload and the hold covers all of it at the output price;
d. the worst case of one call is within one hold, computed from the constants;
e. a cut-off reply is recorded (real cost, else the fallback) and ends UNAVAILABLE, uncharged;
f. mutations that drop max_tokens or the input bound fail here.
"""
import math
import pathlib
import re
import unittest

from app import vision_core, vision_request, vision_wiring
from app.intake import parse
from app.photo_checks import TEXTS
from app.vision_request import AnswerError, ImageRejected
from tests.test_bot_wiring import WiringBase
from tests.test_t4_vision import (ENV, INV, JPEG, PNG, FakeModel, chat, jpeg, parts_of, png,
                                  webp)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def gemini2_tiles(w, h):
    """Public Gemini 2.x image rule: tiles of floor(min side / 1.5) clamped to 256..768."""
    unit = min(768, max(256, math.floor(min(w, h) / 1.5)))
    return math.ceil(w / unit) * math.ceil(h / unit)


def chain(model, environ=ENV, **hooks):
    from app import vision_compare
    return vision_compare.compare_from_env(environ, transport=model, **hooks)


class PricingBasis(unittest.TestCase):
    """4a."""

    def test_deployed_model_is_the_priced_model(self):
        deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        found = re.findall(r"^\s*VISION_MODEL:\s*(\S+)\s*$", deploy, re.M)
        self.assertEqual(found, [vision_request.PRICED_MODEL],
                         "a model swap must redo the per-call ceiling in app/vision_request.py")

    def test_pinned_prices(self):
        self.assertEqual(vision_request.PRICED_MODEL, "google/gemini-3.6-flash")
        self.assertEqual((vision_request.INPUT_USD_PER_M, vision_request.OUTPUT_USD_PER_M), (0.75, 3.75))


class Ceiling(unittest.TestCase):
    """4c, 4d."""

    def test_image_token_bound_covers_both_public_rules(self):
        side = vision_request.MAX_IMAGE_SIDE
        # For a fixed short side the tile count grows with the long side, so the worst
        # image has the long side at the limit.
        worst = max(gemini2_tiles(side, h) for h in range(1, side + 1)) * 258
        self.assertEqual(worst, 3096)
        self.assertLessEqual(max(worst, 1120), vision_request.IMAGE_TOKENS_MAX)

    def test_worst_call_fits_one_hold(self):
        r = vision_request
        tokens_in = r.MAX_IMAGES_PER_CALL * r.IMAGE_TOKENS_MAX + r.MAX_PROMPT_BYTES + r.PROMPT_OVERHEAD_TOKENS
        # the hold is sized for the FULL output limit at the output price, reasoning included
        usd = tokens_in * r.INPUT_USD_PER_M / 1e6 + r.MAX_OUTPUT_TOKENS * r.OUTPUT_USD_PER_M / 1e6
        self.assertAlmostEqual(usd, r.worst_case_call_usd(), places=12)
        self.assertAlmostEqual(usd, 0.019869, places=9)
        self.assertLessEqual(usd, vision_wiring.PER_CALL_FALLBACK_USD)
        self.assertLessEqual(round(usd * 1e6) * vision_wiring.CALLS_PER_CHECK, vision_wiring.HOLD_MICRO)

    def test_real_compare_prompt_fits_the_text_bound(self):
        # 12 attributes with long Russian claims: the largest prompt the chain builds today.
        attrs = [{"attribute_id": "part%02d.detail.colour" % i,
                  "claim": "деталь номер %d товара: тёмно-синий цвет с отливом и швом" % i,
                  "reference_roi": [100, 200, 300, 400]} for i in range(12)]
        validated = vision_core.parse_inventory(chat({"attributes": attrs}))
        for req in vision_core.build_compare_requests(validated, JPEG, PNG, "m/x"):
            used = sum(len(p["text"].encode("utf-8")) for p in parts_of(req) if p["type"] == "text")
            self.assertLess(used, vision_request.MAX_PROMPT_BYTES)


class MaxTokens(unittest.TestCase):
    """4c, 4f: max_tokens in every payload."""

    def test_every_built_payload_has_max_tokens(self):
        validated = vision_core.parse_inventory(chat({"attributes": INV}))
        reqs = [vision_request.build_inventory_request(JPEG, "m/x"),
                *vision_core.build_compare_requests(validated, JPEG, PNG, "m/x")]
        for req in reqs:
            self.assertEqual(req["max_tokens"], vision_request.MAX_OUTPUT_TOKENS)

    def test_every_request_on_the_wire_has_max_tokens(self):
        model = FakeModel(usage={"cost": 0.004})
        self.assertEqual(chain(model)(PNG, JPEG)["status"], "MATCH")
        self.assertEqual(len(model.requests), 3)
        for req in model.requests:
            self.assertEqual(req["max_tokens"], 2800)


class InputBound(unittest.TestCase):
    """4b, 4f: the image side is checked from the header, nothing is re-encoded."""

    def test_side_limit_boundary_for_every_format(self):
        side = vision_request.MAX_IMAGE_SIDE
        self.assertEqual(side, 1536)
        for make in (jpeg, png, webp):
            for w, h in ((side, 385), (385, side), (side, side)):
                vision_request.data_url(make(w, h))
            for w, h in ((side + 1, 10), (10, side + 1), (4096, 4096)):
                with self.subTest(make=make.__name__, w=w, h=h), self.assertRaises(ImageRejected) as cm:
                    vision_request.data_url(make(w, h))
                self.assertEqual(cm.exception.args[0], "SIDE_TOO_LONG")

    def test_image_without_a_readable_size_is_refused(self):
        for data in (b"\xff\xd8\xff\xe0" + b"no-frame-header", b"\x89PNG\r\n\x1a\n" + b"short"):
            with self.assertRaises(ImageRejected) as cm:
                vision_request.data_url(data)
            self.assertEqual(cm.exception.args[0], "NO_SIZE")

    def test_large_image_document_makes_no_provider_call(self):
        big = png(2048, 2048, b"x")
        for reference, candidate in ((big, JPEG), (PNG, big)):
            model = FakeModel(usage={"cost": 0.004})
            costs = []
            with self.assertLogs("vision", "WARNING") as logs:
                result = chain(model, on_call=costs.append, on_begin=lambda: True)(reference, candidate)
            self.assertEqual(result, {"status": "UNAVAILABLE"})
            self.assertEqual((model.requests, costs), ([], []))
            self.assertIn("vision_rejected code=SIDE_TOO_LONG", "\n".join(logs.output))

    def test_telegram_photo_variant_within_the_limit_is_chosen(self):
        sizes = [{"file_id": "s", "file_size": 1500, "width": 90, "height": 68},
                 {"file_id": "m", "file_size": 15000, "width": 320, "height": 240},
                 {"file_id": "x", "file_size": 60000, "width": 800, "height": 600},
                 {"file_id": "y", "file_size": 150000, "width": 1280, "height": 960},
                 {"file_id": "w", "file_size": 600000, "width": 2560, "height": 1920}]
        u = {"message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "photo": sizes}}
        self.assertEqual(parse.parse_update(u)["file_id"], "y")
        sizes[3] = {"file_id": "y", "file_size": 150000, "width": 1537, "height": 960}
        self.assertEqual(parse.parse_update(u)["file_id"], "x")
        u["message"]["photo"] = [{"file_id": "w", "file_size": 9, "width": 4000, "height": 3000}]
        self.assertEqual(parse.parse_update(u)["file_id"], "w")   # nothing fits: refused later

    def test_prompt_and_image_count_bounds(self):
        ok = [vision_request.text_part("a" * vision_request.MAX_PROMPT_BYTES)]
        vision_request.chat_payload("m/x", ok)
        for content in ([vision_request.text_part("a" * (vision_request.MAX_PROMPT_BYTES + 1))],
                        [vision_request.text_part("ж" * (vision_request.MAX_PROMPT_BYTES // 2 + 1))],
                        [vision_request.image_part("data:x")] * 3):
            with self.assertRaises(AnswerError) as cm:
                vision_request.chat_payload("m/x", content)
            self.assertEqual(cm.exception.args[0], "PROMPT_TOO_LONG")

    def test_oversized_inventory_stops_before_the_compare_calls(self):
        long_inv = [{"attribute_id": "a%d" % i, "claim": "x: " + "y" * 600,
                     "reference_roi": [100, 100, 300, 400]} for i in range(12)]
        model = FakeModel(inventory=long_inv, usage={"cost": 0.004})
        costs = []
        with self.assertLogs("vision", "WARNING") as logs:
            result = chain(model, on_call=costs.append)(PNG, JPEG)
        self.assertEqual(result, {"status": "UNAVAILABLE"})
        self.assertEqual((len(model.requests), costs), (1, [0.004]))
        self.assertIn("code=PROMPT_TOO_LONG", "\n".join(logs.output))


class CutOff(unittest.TestCase):
    """4e at the chain level: finish_reason "length" is CUT_OFF, after the cost is reported."""

    def test_cut_off_maps_to_its_own_code(self):
        with self.assertRaises(AnswerError) as cm:
            vision_request.extract_answer(chat({"attributes": INV}, finish="length"))
        self.assertEqual(cm.exception.args[0], "CUT_OFF")


class CutOffModel(FakeModel):
    """The `at`-th reply is cut off at max_tokens: finish_reason "length", a partial text."""

    def __init__(self, at, usage, **kw):
        super().__init__(usage=usage, **kw)
        self.at = at

    def __call__(self, request, headers, timeout):
        reply = super().__call__(request, headers, timeout)
        if len(self.requests) - 1 == self.at:
            choice = reply["body"]["choices"][0]
            choice["finish_reason"], choice["message"]["content"] = "length", '{"attri'
        return reply


class CutOffInTheBot(WiringBase):
    """4e: the day total goes up by the real cost (or the fallback), the verdict is UNAVAILABLE."""

    def factory(self, model):
        return lambda environ, **hooks: chain(model, environ, **hooks)

    def test_cut_off_reply_is_recorded_and_unavailable(self):
        for at, usage, spent in ((0, {"cost": 0.0123}, 0.0123), (1, {"cost": 0.0123}, 0.0246),
                                 (0, None, 0.02), (2, {"completion_tokens": 2800}, 0.06)):
            with self.subTest(at=at, usage=usage):
                self.fresh()
                model = CutOffModel(at, usage)
                checks = self.build(self.factory(model))
                with self.assertLogs("vision", "WARNING") as logs:
                    reply = self.pair(checks)[1]
                self.assertEqual(reply, TEXTS["ru"]["UNAVAILABLE"])
                self.assertEqual(len(model.requests), at + 1)       # nothing after the cut
                self.assertAlmostEqual(self.ledger()["spent_usd"], spent)
                self.assertEqual(self.holds(), {})
                self.assertEqual(self.used(checks), 0)               # the seller is not charged
                self.assertIn("code=CUT_OFF", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
