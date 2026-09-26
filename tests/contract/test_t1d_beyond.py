# tests/contract/test_t1d_beyond.py
"""T1d, beyond the published contract: the claims the docstring makes that the
contract does not read. Run from the repo root:  python3 -m unittest discover -s tests
"""
import os
import stat
import tempfile
import unittest

from app.intake import download as m


class Beyond(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _facts(self, **kw):
        d = {"file_path": "photos/f.jpg", "file_size": None}
        d.update(kw)
        return d

    def test_exactly_at_the_limit_is_not_too_large(self):
        gf = lambda fid: self._facts(file_size=100)
        p = m.download(gf, lambda path: iter([b"x" * 100]), "F", self.dir, 100)
        self.assertEqual(os.path.getsize(p), 100)

    def test_declared_size_absent_is_measured_by_the_stream(self):
        gf = lambda fid: self._facts(file_size=None)
        with self.assertRaises(m.TooLarge):
            m.download(gf, lambda path: iter([b"x" * 50, b"x" * 51]), "F", self.dir, 100)
        self.assertEqual(os.listdir(self.dir), [])

    def test_declared_size_is_not_believed_when_it_lies(self):
        gf = lambda fid: self._facts(file_size=1)          # a lie: says 1 byte
        with self.assertRaises(m.TooLarge):
            m.download(gf, lambda path: iter([b"x" * 101]), "F", self.dir, 100)
        self.assertEqual(os.listdir(self.dir), [])

    def test_zero_limit_refuses_before_fetching(self):
        called = []
        gf = lambda fid: self._facts(file_size=1)
        with self.assertRaises(m.TooLarge):
            m.download(gf, lambda p: called.append(p) or iter([b"x"]), "F", self.dir, 0)
        self.assertEqual(called, [])
        self.assertEqual(os.listdir(self.dir), [])

    def test_empty_stream_writes_an_empty_file(self):
        gf = lambda fid: self._facts(file_size=0)
        p = m.download(gf, lambda path: iter([]), "F", self.dir, 100)
        self.assertEqual(os.path.getsize(p), 0)
        if os.name != "posix":
            self.skipTest("permission bits are POSIX-only")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode) & 0o077, 0)

    def test_fetch_that_raises_before_any_chunk_leaves_nothing(self):
        def boom(path):
            raise RuntimeError("no stream at all")
            yield b""
        gf = lambda fid: self._facts()
        with self.assertRaises(RuntimeError):
            m.download(gf, boom, "F", self.dir, 100)
        self.assertEqual(os.listdir(self.dir), [])

    def test_cleanup_also_on_a_baseexception(self):
        class Stop(Exception):
            pass

        def stop(path):
            yield b"abc"
            raise Stop()
        gf = lambda fid: self._facts()
        with self.assertRaises(Stop):
            m.download(gf, stop, "F", self.dir, 100)
        self.assertEqual(os.listdir(self.dir), [])

    def test_memoryview_and_bytearray_chunks_are_counted_as_bytes(self):
        gf = lambda fid: self._facts()
        p = m.download(gf, lambda path: iter([memoryview(b"ab"), bytearray(b"cd")]),
                       "F", self.dir, 100)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"abcd")

    def test_names_are_not_reused_and_carry_no_remote_bytes(self):
        gf = lambda fid: self._facts(file_size=1)
        seen = set()
        for i in range(64):
            p = m.download(gf, lambda path: iter([b"x"]), "../../etc/passwd\n%00", self.dir, 100)
            name = os.path.basename(p)
            self.assertEqual(name, os.path.basename(os.path.abspath(p)))
            self.assertNotIn("passwd", name)
            self.assertNotIn("\n", name)
            self.assertNotIn("%00", name)
            self.assertNotIn(name, seen)
            seen.add(name)
        self.assertEqual(len(seen), 64)

    def test_returned_path_is_absolute_and_inside_dest(self):
        gf = lambda fid: self._facts(file_size=1)
        p = m.download(gf, lambda path: iter([b"x"]), "F", self.dir, 100)
        self.assertTrue(os.path.isabs(p))
        self.assertEqual(os.path.dirname(p), os.path.abspath(self.dir))

    def test_permissions_hold_under_a_loose_umask(self):
        if os.name != "posix":
            self.skipTest("permission bits are POSIX-only")
        old = os.umask(0o000)
        try:
            gf = lambda fid: self._facts(file_size=1)
            p = m.download(gf, lambda path: iter([b"x"]), "F", self.dir, 100)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        finally:
            os.umask(old)


if __name__ == "__main__":
    unittest.main()
