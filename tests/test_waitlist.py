import os
import tempfile
import unittest

from app import waitlist


class WaitlistTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        waitlist.DATA_DIR = self.dir.name
        waitlist.PATH = os.path.join(self.dir.name, "waitlist.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_source_from_payload(self):
        self.assertEqual(waitlist.record(1, "/start landing_ru"), ("landing_ru", True))

    def test_no_payload_is_direct(self):
        self.assertEqual(waitlist.record(2, "/start"), ("direct", True))

    def test_bad_payload_is_direct(self):
        self.assertEqual(waitlist.record(3, "/start <script>"), ("direct", True))

    def test_first_source_wins_and_counts_unique_users(self):
        waitlist.record(4, "/start tg_ads_1")
        self.assertEqual(waitlist.record(4, "/start landing_en"), ("landing_en", False))
        waitlist.record(5, "/start tg_ads_1")
        self.assertEqual(waitlist.counts(), {"tg_ads_1": 2})


if __name__ == "__main__":
    unittest.main()
