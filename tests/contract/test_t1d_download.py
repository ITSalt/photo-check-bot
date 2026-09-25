"""T1d contract: app/intake/download.py -> download(get_file, fetch, file_id, dest_dir, max_bytes) -> str

get_file(file_id) -> {"file_path": str, "file_size": int | None}   (Telegram getFile result)
fetch(file_path)  -> iterable of bytes chunks                        (the file body)
Raises app.intake.download.TooLarge when the declared or the streamed size exceeds max_bytes.
"""
import os
import stat
import tempfile
import unittest

from app.intake import download as m


def fake(file_size, chunks):
    return (lambda fid: {"file_path": "photos/f.jpg", "file_size": file_size}), (lambda path: iter(chunks))


@unittest.skipUnless(getattr(m, "IMPLEMENTED", False), "T1d not implemented yet")
class Download(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_file_and_returns_path_inside_dest(self):
        gf, fe = fake(6, [b"abc", b"def"])
        p = m.download(gf, fe, "F1", self.dir, max_bytes=100)
        self.assertEqual(os.path.dirname(os.path.abspath(p)), os.path.abspath(self.dir))
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"abcdef")

    def test_random_name_not_derived_from_file_id_or_path(self):
        gf, fe = fake(1, [b"x"])
        p = m.download(gf, fe, "../../etc/passwd", self.dir, max_bytes=100)
        self.assertNotIn("passwd", os.path.basename(p))
        self.assertNotIn("f.jpg", os.path.basename(p))

    def test_owner_only_permissions_on_posix(self):
        if os.name != "posix":
            self.skipTest("permission bits are POSIX-only")
        gf, fe = fake(1, [b"x"])
        p = m.download(gf, fe, "F", self.dir, max_bytes=100)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode) & 0o077, 0)

    def test_declared_size_too_large_fetches_nothing(self):
        called = []
        gf = lambda fid: {"file_path": "p", "file_size": 101}
        fe = lambda path: called.append(path) or iter([b"x"])
        with self.assertRaises(m.TooLarge):
            m.download(gf, fe, "F", self.dir, max_bytes=100)
        self.assertEqual(called, [])
        self.assertEqual(os.listdir(self.dir), [])

    def test_streamed_size_over_limit_aborts_and_leaves_nothing(self):
        gf, fe = fake(None, [b"x" * 60, b"x" * 60])
        with self.assertRaises(m.TooLarge):
            m.download(gf, fe, "F", self.dir, max_bytes=100)
        self.assertEqual(os.listdir(self.dir), [])

    def test_fetch_error_leaves_nothing(self):
        def boom(path):
            yield b"abc"
            raise IOError("network")
        gf = lambda fid: {"file_path": "p", "file_size": None}
        with self.assertRaises(IOError):
            m.download(gf, boom, "F", self.dir, max_bytes=100)
        self.assertEqual(os.listdir(self.dir), [])


if __name__ == "__main__":
    unittest.main()
