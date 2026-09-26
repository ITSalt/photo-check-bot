"""The landing builds from content/*.json and the output stays static and self-contained."""
import importlib.util
import pathlib
import re
import unittest

LANDING = pathlib.Path(__file__).resolve().parent.parent / "landing"
spec = importlib.util.spec_from_file_location("landing_build", LANDING / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildTest(unittest.TestCase):
    def test_pages_render(self):
        for lang, other in (("ru", "en"), ("en", "ru")):
            html = build.page(lang, other)
            self.assertIn(f'<html lang="{lang}">', html)
            self.assertIn(f"start=landing_{lang}", html)
            # The only script is our inline ?src passthrough: no external scripts, trackers or frames.
            self.assertEqual(len(re.findall(r"<script\b", html)), 1)
            self.assertNotRegex(html, r"<script[^>]*\bsrc=|<iframe|<link[^>]*stylesheet")

    def test_links_only_to_known_hosts(self):
        for lang, other in (("ru", "en"), ("en", "ru")):
            for url in re.findall(r'(?:href|content)="(https?://[^"]+)"', build.page(lang, other)):
                self.assertTrue(url.startswith(("https://t.me/", "https://looktwinstudio.ru/", build.SITE + "/")), url)

    def test_segment_buttons_carry_their_tag(self):
        c = build.load("ru")
        for s in c["segments"]["items"]:
            self.assertIn(f'start=landing_ru__{s["seg"]}" data-seg="{s["seg"]}"', build.page("ru", "en"))

    def test_committed_pages_are_up_to_date(self):
        # CI rebuilds on deploy anyway; this catches a PR that edited content but forgot to rebuild.
        for lang, other in (("ru", "en"), ("en", "ru")):
            committed = (LANDING / lang / "index.html").read_text(encoding="utf-8")
            self.assertEqual(committed, build.page(lang, other), f"run: python landing/build.py ({lang})")


if __name__ == "__main__":
    unittest.main()
