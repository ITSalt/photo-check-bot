"""T1b contract: app/intake/pairing.py -> step(state, event, now) -> (new_state, actions)"""
import unittest

from app.intake import pairing as m

T0 = 1_000_000.0


def img(fid, caption=""):
    return {"kind": "photo", "file_id": fid, "caption": caption}


def cmd(name):
    return {"kind": "command", "command": name, "arg": None}


def says(actions):
    return [a["say"] for a in actions if "say" in a]


@unittest.skipUnless(getattr(m, "IMPLEMENTED", False), "T1b not implemented yet")
class Pairing(unittest.TestCase):
    def test_start_resets_and_asks_for_generated(self):
        st, acts = m.step({"stage": "have_generated", "generated": "g", "real": None, "updated": T0}, cmd("start"), T0)
        self.assertEqual(st["stage"], "idle")
        self.assertEqual(says(acts), ["ask_generated"])

    def test_default_order_generated_then_real(self):
        st, acts = m.step(None, img("g1"), T0)
        self.assertEqual((st["stage"], st["generated"]), ("have_generated", "g1"))
        self.assertEqual(says(acts), ["ask_real"])
        st, acts = m.step(st, img("r1"), T0 + 10)
        self.assertEqual(st["stage"], "idle")
        self.assertIn({"do": "check", "generated": "g1", "real": "r1"}, acts)

    def test_caption_marks_real_first(self):
        st, acts = m.step(None, img("r1", caption="настоящая вещь"), T0)
        self.assertEqual((st["stage"], st["real"]), ("have_real", "r1"))
        self.assertEqual(says(acts), ["ask_generated"])
        st, acts = m.step(st, img("g1"), T0 + 5)
        self.assertIn({"do": "check", "generated": "g1", "real": "r1"}, acts)

    def test_caption_keywords_both_languages(self):
        for cap in ("real", "REAL item", "оригинал", "настоящий"):
            self.assertEqual(m.step(None, img("x", cap), T0)[0]["stage"], "have_real", cap)
        for cap in ("ai", "AI render", "нейро", "генерация"):
            self.assertEqual(m.step(None, img("x", cap), T0)[0]["stage"], "have_generated", cap)

    def test_two_labelled_photos_complete_the_pair(self):
        # Reported by small-useful-steps on the board: a labelled second photo must complete the pair, in either order.
        st, _ = m.step(None, img("g1", caption="ai"), T0)
        st, acts = m.step(st, img("r1", caption="real"), T0 + 1)
        self.assertIn({"do": "check", "generated": "g1", "real": "r1"}, acts)
        self.assertEqual(st["stage"], "idle")
        st, _ = m.step(None, img("r1", caption="оригинал"), T0)
        st, acts = m.step(st, img("g1", caption="нейро"), T0 + 1)
        self.assertIn({"do": "check", "generated": "g1", "real": "r1"}, acts)
        self.assertEqual(st["stage"], "idle")

    def test_latin_markers_are_whole_words(self):
        # "ai" inside "detail" or "plain" is not a label: the second photo of a default pair is the real one.
        for cap in ("detail of the sleeve", "plain back view", "really close"):
            st, _ = m.step(None, img("g1"), T0)
            st, acts = m.step(st, img("r1", caption=cap), T0 + 1)
            self.assertIn({"do": "check", "generated": "g1", "real": "r1"}, acts, cap)

    def test_russian_markers_are_stems(self):
        # "ген" also matches "сгенерированное": the photo replaces the generated half instead of completing the pair.
        st, _ = m.step(None, img("g1"), T0)
        st, acts = m.step(st, img("g2", caption="сгенерированное фото"), T0 + 1)
        self.assertEqual((st["stage"], st["generated"]), ("have_generated", "g2"))
        self.assertEqual(says(acts), ["ask_real"])
        self.assertFalse([a for a in acts if a.get("do") == "check"])

    def test_two_generated_in_a_row_replaces_and_asks_real_again(self):
        st, _ = m.step(None, img("g1"), T0)
        st, acts = m.step(st, img("g2", caption="ai"), T0 + 1)
        self.assertEqual((st["stage"], st["generated"]), ("have_generated", "g2"))
        self.assertEqual(says(acts), ["ask_real"])

    def test_stale_half_pair_expires(self):
        st, _ = m.step(None, img("g1"), T0)
        st, acts = m.step(st, img("g2"), T0 + 1801)
        self.assertEqual(says(acts)[0], "expired")
        self.assertEqual((st["stage"], st["generated"]), ("have_generated", "g2"))

    def test_cancel(self):
        st, _ = m.step(None, img("g1"), T0)
        st, acts = m.step(st, cmd("cancel"), T0 + 1)
        self.assertEqual((st["stage"], st["generated"], st["real"]), ("idle", None, None))
        self.assertEqual(says(acts), ["cancelled"])

    def test_unsupported_and_text(self):
        st, acts = m.step(None, {"kind": "unsupported"}, T0)
        self.assertEqual(says(acts), ["unsupported"])
        st, acts = m.step(None, {"kind": "text"}, T0)
        self.assertEqual(says(acts), ["ask_generated"])

    def test_image_doc_counts_as_image(self):
        st, _ = m.step(None, {"kind": "image_doc", "file_id": "d1", "caption": ""}, T0)
        self.assertEqual(st["generated"], "d1")

    def test_state_is_json_serialisable_and_input_not_mutated(self):
        import json
        s0 = {"stage": "have_generated", "generated": "g", "real": None, "updated": T0}
        snap = dict(s0)
        st, _ = m.step(s0, img("r"), T0 + 1)
        json.dumps(st)
        self.assertEqual(s0, snap)


if __name__ == "__main__":
    unittest.main()
