"""WP-BOT-14: Session.delete_all tries every file and raises without a path.

The four tests of class SessionDeleteHardening are fable-terminal's (board, adapted to ours);
the rest are WP-BOT-14 additions. Fakes only: no network, no keys.
"""
import errno, os, stat, tempfile, traceback, unittest, glob
from unittest import mock
from app import session_factory as sf

REF, CAND = b"\xff\xd8reference" * 50, b"\xff\xd8candidate" * 70
class FakeTelegram:
    def __init__(self, files, chunk=64): self.files, self.chunk = files, chunk
    def get_file(self, fid):
        data, declared, _ = self.files[fid]; return {"file_path": "photos/" + fid + ".jpg", "file_size": declared}
    def fetch(self, fp):
        fid = fp[len("photos/"):-len(".jpg")]; data = self.files[fid][0]
        for k in range(0, len(data), self.chunk): yield data[k:k + self.chunk]

class SessionDeleteHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "photos"); os.mkdir(self.dir, 0o700)
    def factory(self):
        tg = FakeTelegram({"r": (REF, None, None), "c": (CAND, None, None)})
        return sf.make_session_factory(tg.get_file, tg.fetch, self.dir, 4096)
    def files(self): return [p for p in glob.glob(os.path.join(self.dir, "**"), recursive=True) if os.path.isfile(p)]
    def entries(self): return os.listdir(self.dir)

    def test_interrupt_during_second_still_cleans(self):
        f = self.factory(); real = sf.download; calls = []
        def flaky(*a):
            calls.append(1)
            if len(calls) == 2: raise KeyboardInterrupt
            return real(*a)
        with mock.patch.object(sf, "download", flaky), self.assertRaises(KeyboardInterrupt):
            f("r", "c")
        self.assertEqual(self.entries(), [])

    def test_undeletable_file_is_not_silent(self):
        s = self.factory()("r", "c")
        os.chmod(s.directory, 0o500)
        try:
            if os.access(s.directory, os.W_OK): self.skipTest("running as root")
            with self.assertRaises(OSError) as cm:
                s.delete_all()
            tb = "".join(traceback.format_exception(cm.exception))
            self.assertNotIn(self.dir, tb, "path in traceback")
        finally:
            os.chmod(s.directory, 0o700)
        s.delete_all()
        self.assertEqual(self.entries(), [])

    def test_undeletable_file_others_still_attempted(self):
        s = self.factory()("r", "c")
        real = os.remove
        def rm(p):
            if p == s.reference_path: raise PermissionError(13, "denied", p)
            return real(p)
        with mock.patch.object(sf.os, "remove", rm):
            with self.assertRaises(OSError):
                s.delete_all()
        self.assertFalse(os.path.exists(s.candidate_path), "second file not attempted")

    def test_path_outside_dest_dir_is_refused_and_left_alone(self):
        outside = os.path.join(self.tmp.name, "elsewhere.bin")
        def rogue(get_file, fetch, file_id, dest_dir, max_bytes):
            open(outside, "wb").close(); return outside
        with mock.patch.object(sf, "download", rogue), self.assertRaises(OSError):
            self.factory()("r", "c")
        self.assertTrue(os.path.exists(outside))   # WP-UX-04: never unlink outside the session

    def test_failed_delete_error_has_no_path_and_all_steps_are_attempted(self):
        s = self.factory()("r", "c")
        real, removed, rmtrees = os.remove, [], []
        def rm(p):
            removed.append(p)
            if p == s.reference_path: raise PermissionError(errno.EACCES, "denied", p)
            return real(p)
        with mock.patch.object(sf.os, "remove", rm), \
                mock.patch.object(sf.shutil, "rmtree", side_effect=lambda d: rmtrees.append(d)):
            with self.assertRaises(OSError) as cm:
                s.delete_all()
        self.assertEqual(removed, [s.reference_path, s.candidate_path])
        self.assertEqual(rmtrees, [s.directory])              # the directory was attempted too
        err = cm.exception
        self.assertEqual(err.errno, errno.EACCES)
        for text in (str(err), repr(err), "".join(traceback.format_exception(err))):
            self.assertNotIn(self.dir, text)
            self.assertNotIn(os.path.basename(s.reference_path), text)
            self.assertNotIn(os.path.basename(s.directory), text)
        self.assertIsNone(err.__cause__)
        self.assertTrue(err.__suppress_context__)
        sf.shutil.rmtree(s.directory)

    def test_directory_removal_failure_is_raised_without_path(self):
        s = self.factory()("r", "c")
        with mock.patch.object(sf.shutil, "rmtree", side_effect=PermissionError(13, "x", s.directory)):
            with self.assertRaises(OSError) as cm:
                s.delete_all()
        self.assertNotIn(s.directory, str(cm.exception) + repr(cm.exception))
        s.delete_all()                                         # retry once the cause is gone
        self.assertEqual(self.entries(), [])

    def test_all_removable_no_error_and_directory_gone(self):
        s = self.factory()("r", "c")
        s.delete_all()
        self.assertEqual(self.entries(), [])
        s.delete_all()                                         # idempotent
