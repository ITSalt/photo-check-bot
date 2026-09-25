"""T1f contract: app/verdict/format.py -> format_verdict(results, lang="ru", stub=False) -> str

results: list of {"detail": str, "verdict": "MATCH"|"MISMATCH"|"NOT_VISIBLE_IN_REFERENCE"|"ASYMMETRIC_PASS",
                  "reason": str, "fix": str | None}
"""
import re
import unittest

from app.verdict import format as m

R = [
    {"detail": "пуговицы", "verdict": "MISMATCH", "reason": "на фото 3, у вещи 4", "fix": "перегенерировать с 4 пуговицами"},
    {"detail": "принт", "verdict": "ASYMMETRIC_PASS", "reason": "совпадает только в одном порядке сравнения", "fix": None},
    {"detail": "спинка", "verdict": "NOT_VISIBLE_IN_REFERENCE", "reason": "не показана на фото вещи", "fix": None},
    {"detail": "цвет", "verdict": "MATCH", "reason": "совпадает", "fix": None},
    {"detail": "молния", "verdict": "MATCH", "reason": "совпадает", "fix": None},
]


@unittest.skipUnless(getattr(m, "IMPLEMENTED", False), "T1f not implemented yet")
class VerdictFormat(unittest.TestCase):
    def test_problems_before_missing_before_matches(self):
        t = m.format_verdict(R, "ru")
        self.assertLess(t.index("пуговицы"), t.index("спинка"))
        self.assertLess(t.index("принт"), t.index("спинка"))
        self.assertLess(t.index("спинка"), t.index("цвет"))

    def test_fix_is_shown_for_mismatch(self):
        self.assertIn("перегенерировать с 4 пуговицами", m.format_verdict(R, "ru"))

    def test_asymmetric_is_never_reported_as_match(self):
        t = m.format_verdict([R[1]], "ru")
        self.assertNotRegex(t.lower(), r"совпада[её]т\b(?!.*одном)")  # it may quote the reason, but not claim a match
        self.assertIn("принт", t)

    def test_not_visible_asks_for_a_photo(self):
        t = m.format_verdict([R[2]], "ru")
        self.assertRegex(t.lower(), r"пришлите.*фото")
        t = m.format_verdict([R[2]], "en")
        self.assertRegex(t.lower(), r"send .*photo")

    def test_no_percentages(self):
        self.assertIsNone(re.search(r"\d\s*%", m.format_verdict(R, "ru")))

    def test_all_match_says_so_briefly(self):
        t = m.format_verdict([R[3], R[4]], "en")
        self.assertIn("2", t)
        self.assertLessEqual(len(t), 400)

    def test_stub_label(self):
        self.assertRegex(m.format_verdict(R, "ru", stub=True), r"(?i)демо")
        self.assertRegex(m.format_verdict(R, "en", stub=True), r"(?i)demo")
        self.assertNotRegex(m.format_verdict(R, "en", stub=False), r"(?i)demo")

    def test_telegram_length_limit(self):
        many = [{"detail": f"деталь {i}", "verdict": "MISMATCH", "reason": "x" * 200, "fix": "y" * 200} for i in range(60)]
        self.assertLessEqual(len(m.format_verdict(many, "ru")), 4096)

    def test_empty_results(self):
        self.assertTrue(m.format_verdict([], "ru").strip())

    def test_unknown_lang_falls_back_to_english(self):
        self.assertEqual(m.format_verdict([R[2]], "de"), m.format_verdict([R[2]], "en"))


if __name__ == "__main__":
    unittest.main()
