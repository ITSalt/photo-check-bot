"""Rules for landing texts (landing/content/*.json) and pictures (landing/assets/*.svg).

Standalone: needs only the two JSON files and the assets folder, so a contributor
can run it without the rest of the repo:  python -m unittest tests.test_landing_content
"""
import json
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET

LANDING = pathlib.Path(__file__).resolve().parent.parent / "landing"
LANGS = ("ru", "en")
SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
LINK_HOSTS = ("https://looktwinstudio.ru/", "https://t.me/", "https://itsalt.github.io/photo-check-bot/")

# Maximum length in characters for every text key. A key missing here is a schema error.
LIMITS = {
    "meta.title": 70, "meta.desc": 170, "meta.og_image": 40,
    "header.brand": 40, "header.lang_other": 20,
    "hero.kicker": 90, "hero.h1": 80, "hero.lead": 260, "hero.cta": 40, "hero.cta_note": 180, "hero.price_note": 140,
    "illustration.asset": 60, "illustration.alt": 250, "illustration.caption": 160,
    "problems.h": 80, "problems.items[].title": 40, "problems.items[].text": 170,
    "how.h": 40, "how.steps[]": 140,
    "how.mockup.asset": 60, "how.mockup.alt": 250, "how.mockup.caption": 160,
    "checks.h": 40, "checks.groups[].title": 40, "checks.groups[].items[]": 70, "checks.note": 200,
    "segments.h": 80, "segments.items[].title": 60, "segments.items[].text": 200,
    "segments.items[].cta": 40, "segments.items[].seg": 8,
    "who.h": 40, "who.text": 450, "who.link_text": 30, "who.link_url": 60,
    "why_telegram": 240,
    "status.text": 220, "status.cta": 40,
    "privacy.block": 400, "privacy.page_link": 40,
    "privacy_page.title": 80, "privacy_page.sections[].h": 80, "privacy_page.sections[].p": 700,
}
# Keys that must not be empty (everything else may be "" to hide its block).
REQUIRED = {"meta.title", "meta.desc", "header.brand", "header.lang_other", "hero.kicker", "hero.h1", "hero.lead",
            "hero.cta", "hero.cta_note", "problems.h", "problems.items[].title", "problems.items[].text", "how.h",
            "how.steps[]", "checks.h", "checks.groups[].items[]", "who.h", "who.text", "status.text", "status.cta",
            "privacy.block"}
# (min, max) number of entries for every list.
COUNTS = {"problems.items": (3, 4), "how.steps": (3, 4), "checks.groups": (1, 4), "checks.groups[].items": (2, 8),
          "segments.items": (0, 3), "privacy_page.sections": (0, 8)}
# Keys that may contain a URL.
URL_KEYS = {"who.link_url"}


def load(lang):
    return json.loads((LANDING / "content" / f"{lang}.json").read_text(encoding="utf-8"))


def walk(node, path=""):
    """Yield (schema_path, value) for every leaf; list positions become []."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        yield path, node
        for v in node:
            yield from walk(v, path + "[]")
    else:
        yield path, node


def shape(node):
    if isinstance(node, dict):
        return {k: shape(v) for k, v in node.items()}
    if isinstance(node, list):
        return [shape(v) for v in node]
    return type(node).__name__


class ContentTest(unittest.TestCase):
    def setUp(self):
        self.c = {lang: load(lang) for lang in LANGS}

    def test_same_structure_in_both_languages(self):
        # Same keys, same number of cards, groups, steps and sections: RU and EN stay parallel.
        self.assertEqual(shape(self.c["ru"]), shape(self.c["en"]))

    def test_every_key_is_known_and_within_limits(self):
        for lang, c in self.c.items():
            for path, value in walk(c):
                if isinstance(value, list):
                    lo, hi = COUNTS[path]
                    self.assertTrue(lo <= len(value) <= hi, f"{lang}: {path} has {len(value)} entries, allowed {lo}..{hi}")
                    continue
                self.assertIn(path, LIMITS, f"{lang}: unknown key {path}")
                self.assertIsInstance(value, str, f"{lang}: {path} must be a string")
                self.assertLessEqual(len(value), LIMITS[path], f"{lang}: {path} is {len(value)} chars, limit {LIMITS[path]}")
                if path in REQUIRED:
                    self.assertTrue(value, f"{lang}: {path} must not be empty")

    def test_plain_text_only(self):
        for lang, c in self.c.items():
            for path, value in walk(c):
                if not isinstance(value, str):
                    continue
                self.assertEqual(value, value.strip(), f"{lang}: {path} has leading/trailing spaces")
                self.assertNotIn("  ", value, f"{lang}: {path} has a double space")
                self.assertFalse(re.search(r"[<>]", value), f"{lang}: {path} contains < or >; texts are plain, no HTML")
                if path not in URL_KEYS:
                    self.assertNotRegex(value.lower(), r"https?://|www\.|javascript:", f"{lang}: {path} contains a link")

    def test_links_go_to_known_hosts(self):
        for lang, c in self.c.items():
            for path, value in walk(c):
                if path in URL_KEYS and value:
                    self.assertTrue(value.startswith(LINK_HOSTS), f"{lang}: {path} = {value}")

    def test_blocks_are_complete_or_empty(self):
        for lang, c in self.c.items():
            for block in (c["illustration"], c["how"]["mockup"]):
                if block["asset"]:
                    self.assertTrue(block["alt"], f"{lang}: a picture needs alt text")
                else:
                    self.assertEqual(block["alt"] + block["caption"], "", f"{lang}: alt/caption without a picture")
            if c["segments"]["items"]:
                self.assertTrue(c["segments"]["h"], f"{lang}: segments need a heading")
                for s in c["segments"]["items"]:
                    self.assertTrue(all(s.values()), f"{lang}: every segment field is required")
            if c["privacy_page"]["sections"]:
                self.assertTrue(c["privacy_page"]["title"] and c["privacy"]["page_link"],
                                f"{lang}: a privacy page needs a title and a footer link text")

    def test_segment_tags(self):
        # Segment buttons open the bot with /start landing_<lang>__<seg>; the bot keeps the first tag per user.
        for lang, c in self.c.items():
            segs = [s["seg"] for s in c["segments"]["items"]]
            self.assertEqual(len(segs), len(set(segs)), f"{lang}: duplicate segment tags")
            for seg in segs:
                self.assertRegex(seg, r"^[a-z]{2,8}$", f"{lang}: segment tag {seg!r}")
        self.assertEqual([s["seg"] for s in self.c["ru"]["segments"]["items"]],
                         [s["seg"] for s in self.c["en"]["segments"]["items"]], "segment tags must match across languages")

    def test_referenced_assets_exist(self):
        for lang, c in self.c.items():
            for name in (c["illustration"]["asset"], c["how"]["mockup"]["asset"]):
                if name:
                    self.assertRegex(name, r"^assets/[a-z0-9-]+\.svg$", f"{lang}: asset path {name!r}")
                    self.assertTrue((LANDING / name).is_file(), f"{lang}: {name} is missing")
            og = c["meta"]["og_image"]
            if og:
                self.assertEqual(og, f"assets/og-{lang}.png", f"{lang}: og_image must be assets/og-{lang}.png")
                self.assertTrue((LANDING / og).is_file(), f"{lang}: {og} is missing")


class AssetTest(unittest.TestCase):
    """SVGs are inlined into the page, so they must be inert and must not clash with each other."""

    FORBIDDEN = {"script", "foreignObject", "iframe", "embed", "object", "image", "style", "a", "feImage", "handler"}

    def svgs(self):
        return sorted((LANDING / "assets").glob("*.svg"))

    def test_assets_are_safe_and_self_contained(self):
        for f in self.svgs():
            raw = f.read_text(encoding="utf-8")
            with self.subTest(asset=f.name):
                self.assertLessEqual(len(raw.encode("utf-8")), 40_000, "keep each SVG under 40 KB")
                self.assertNotRegex(raw, r"(?i)<!DOCTYPE|<!ENTITY", "no DTD or entities")
                self.assertNotRegex(raw.lower(), r"javascript:|data:|@import|url\((?!#)", "no external or scriptable references")
                root = ET.fromstring(raw)
                self.assertEqual(root.tag, SVG_NS + "svg", "root element must be <svg> in the SVG namespace")
                self.assertTrue(root.get("viewBox"), "the root <svg> needs a viewBox")
                prefix = f.stem + "-"
                for el in root.iter():
                    tag = el.tag.split("}")[-1]
                    self.assertNotIn(tag, self.FORBIDDEN, f"<{tag}> is not allowed")
                    for attr, value in el.attrib.items():
                        name = attr.split("}")[-1]
                        self.assertFalse(name.lower().startswith("on"), f"event attribute {name}")
                        if name == "href" or attr == XLINK_HREF:
                            self.assertTrue(value.startswith("#"), f"only local #id references, got {value!r}")
                        if name == "id":
                            self.assertTrue(value.startswith(prefix), f"id {value!r} must start with {prefix!r}")

    def test_social_previews_have_the_right_size(self):
        for f in self.svgs():
            if f.name.startswith("og-"):
                root = ET.fromstring(f.read_text(encoding="utf-8"))
                self.assertEqual(root.get("viewBox"), "0 0 1200 630", f"{f.name}: social preview is 1200x630")


if __name__ == "__main__":
    unittest.main()
