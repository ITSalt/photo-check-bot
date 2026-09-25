"""T1e contract: app/intake/tempfiles.py -> TempPair (context manager) and sweep(dir, max_age_s, now) -> int"""
import os
import tempfile
import unittest

from app.intake import tempfiles as m


@unittest.skipUnless(getattr(m, "IMPLEMENTED", False), "T1e not implemented yet")
class TempFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def mk(self, name, age=0, now=1_000_000.0):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(b"x")
        os.utime(p, (now - age, now - age))
        return p

    def test_pair_deleted_on_normal_exit(self):
        a, b = self.mk("a"), self.mk("b")
        with m.TempPair(a, b) as pair:
            self.assertEqual((pair.generated, pair.real), (a, b))
        self.assertFalse(os.path.exists(a) or os.path.exists(b))

    def test_pair_deleted_on_exception_and_exception_propagates(self):
        a, b = self.mk("a"), self.mk("b")
        with self.assertRaises(ValueError):
            with m.TempPair(a, b):
                raise ValueError("check failed")
        self.assertFalse(os.path.exists(a) or os.path.exists(b))

    def test_missing_file_on_exit_is_not_an_error(self):
        a, b = self.mk("a"), self.mk("b")
        with m.TempPair(a, b):
            os.remove(a)
        self.assertFalse(os.path.exists(b))

    def test_none_paths_allowed(self):
        a = self.mk("a")
        with m.TempPair(a, None):
            pass
        self.assertFalse(os.path.exists(a))

    def test_sweep_removes_only_old_files(self):
        now = 1_000_000.0
        old1, old2, fresh = self.mk("o1", 4000, now), self.mk("o2", 3601, now), self.mk("f", 100, now)
        os.mkdir(os.path.join(self.dir, "sub"))
        self.assertEqual(m.sweep(self.dir, 3600, now), 2)
        self.assertTrue(os.path.exists(fresh))
        self.assertFalse(os.path.exists(old1) or os.path.exists(old2))
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "sub")))

    def test_sweep_missing_dir_returns_zero(self):
        self.assertEqual(m.sweep(os.path.join(self.dir, "nope"), 60, 1.0), 0)


if __name__ == "__main__":
    unittest.main()
