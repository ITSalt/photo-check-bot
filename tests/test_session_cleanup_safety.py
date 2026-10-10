"""WP-UX-04: never delete a file outside the session; a failed cleanup is logged, path-free."""
import errno
import glob
import os
import tempfile
import unittest
from unittest import mock

from app import session_factory as sf

REF, CAND = b"\xff\xd8reference" * 50, b"\xff\xd8candidate" * 70


class FakeTelegram:
    def __init__(self, break_on=None):
        self.files, self.break_on = {"r": REF, "c": CAND}, break_on

    def get_file(self, fid):
        return {"file_path": "photos/" + fid + ".jpg", "file_size": len(self.files[fid])}

    def fetch(self, fp):
        fid = fp[len("photos/"):-len(".jpg")]
        data = self.files[fid]
        yield data[:10]
        if fid == self.break_on:
            raise OSError("broken stream")
        yield data[10:]


class SessionCleanupSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "photos")
        os.mkdir(self.root, 0o700)

    def test_rogue_download_outside_path_is_never_unlinked(self):
        sentinel = os.path.join(self.tmp.name, "sentinel.bin")
        with open(sentinel, "wb") as f:
            f.write(b"precious")
        tg = FakeTelegram()
        real = sf.download
        calls = []

        def rogue(*a):
            calls.append(1)
            return sentinel if len(calls) == 2 else real(*a)

        factory = sf.make_session_factory(tg.get_file, tg.fetch, self.root, 4096)
        with mock.patch.object(sf, "download", rogue), self.assertRaises(OSError) as cm:
            factory("r", "c")
        self.assertEqual(cm.exception.errno, errno.EINVAL)
        self.assertNotIn(sentinel, str(cm.exception))
        with open(sentinel, "rb") as f:
            self.assertEqual(f.read(), b"precious")
        self.assertEqual(os.listdir(self.root), [])

    def test_failed_cleanup_logged_once_without_path_and_swept_later(self):
        tg = FakeTelegram(break_on="c")
        factory = sf.make_session_factory(tg.get_file, tg.fetch, self.root, 4096)

        def denied(*a, **k):
            raise PermissionError(errno.EACCES, "denied")

        with mock.patch("os.unlink", denied), mock.patch("os.remove", denied), \
                self.assertLogs("session_factory", level="DEBUG") as logs, \
                self.assertRaises(OSError) as cm:
            factory("r", "c")
        self.assertEqual(str(cm.exception), "broken stream")   # the original exception leaves
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].getMessage(), "session_cleanup_incomplete")
        self.assertIsNone(logs.records[0].exc_info)
        remaining = os.listdir(self.root)
        self.assertEqual(len(remaining), 1)                    # directory remains
        self.assertNotIn(remaining[0], logs.output[0])
        self.assertNotIn(self.root, logs.output[0])
        sf.prepare_root(self.root)
        self.assertEqual(os.listdir(self.root), [])

    def test_clean_failure_path_logs_nothing(self):
        tg = FakeTelegram(break_on="c")
        factory = sf.make_session_factory(tg.get_file, tg.fetch, self.root, 4096)
        with self.assertNoLogs("session_factory", level="DEBUG"), self.assertRaises(OSError):
            factory("r", "c")
        self.assertEqual(glob.glob(os.path.join(self.root, "*")), [])


if __name__ == "__main__":
    unittest.main()
