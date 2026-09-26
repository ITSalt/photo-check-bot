"""Two defects in the T1d writer that the contract's own tests cannot see.

Both are about the gap between the moment the name is chosen and the moment the
bytes are there:

  * `open(path, "wb")` then `os.chmod(path, 0o600)` -- between the two calls the
    file exists with the process umask (0o644 with umask 022), so anything that
    can read the directory can read the picture;
  * `_new_path` checks `os.path.exists` and `open` creates: a file that appears
    in that gap is truncated by the write.

Neither is exploitable in a private tmpfs with one user, which is why the
maintainers filed them as hardening. They are measurable here, and the repair is
one call: `os.open(path, O_CREAT | O_EXCL | O_WRONLY, 0o600)`.

Run from the repo root:  python3 -m unittest discover -s tests
Against the unhardened writer three arms fail: the portable one sees `chmod` applied to
a file that already exists, the POSIX one shows the mode the file had just before
`chmod` (0o644 against 0o600), and the race one shows the sentinel's bytes replaced.
Against the repaired writer all pass. Only the mode-bits arm skips on a system without
POSIX permission bits; the other two measure the same two defects there as well.
Standard library only, like the rest of the repository.
"""
import os
import secrets
import stat
import tempfile
import time
import unittest
from unittest import mock

from app.intake import download as dl


class Window(unittest.TestCase):
    def setUp(self):
        old = os.umask(0o022)          # the fixture page's umask, kept for the run
        self.addCleanup(os.umask, old)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def mode_when_chmod_was_called(self, path, mode):
        """The mode the file had while `chmod` was being called, and the mode asked for."""
        try:
            before = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            before = None
        return before, mode

    def test_the_mode_is_set_when_the_file_is_created(self):
        """Portable: the writer never applies the mode to a file that already exists.

        This arm reads no permission bits, so it runs on Windows as well. It asks the
        shape of the repair directly: `stat` can only tell you what the mode is when
        you look, while the defect is that the mode is applied *after* the file exists
        and there is an instant when the file carries the umask's instead. A writer
        that repairs this without `chmod` passes here wherever it runs; a writer that
        keeps `chmod` fails here wherever it runs.
        """
        calls = []
        with mock.patch.object(os, "chmod", lambda *a, **kw: calls.append(a)):
            dl.download(lambda fid: {"file_path": "p", "file_size": 4},
                        lambda fp: [b"abcd"], "fid", self.dir, 1024)
        self.assertEqual(
            calls, [],
            "the writer applied the mode to a file that already existed")

    def test_the_file_is_owner_only_from_the_moment_it_exists(self):
        if os.name != "posix":
            self.skipTest("permission bits are POSIX-only; the portable arm above "
                          "measures the same repair where they do not exist")
        seen = []
        real_chmod = os.chmod

        def watched_chmod(path, mode, **kw):
            # A probe, not the defect: it makes the window long enough to read. Any
            # delay a real chmod can have (a loaded box, a network filesystem) makes
            # the same window real, and the mode below is the file's, not a guess.
            seen.append(self.mode_when_chmod_was_called(path, mode))
            time.sleep(0.05)
            return real_chmod(path, mode, **kw)

        with mock.patch.object(os, "chmod", watched_chmod):
            path = dl.download(lambda fid: {"file_path": "p", "file_size": 4},
                               lambda fp: [b"abcd"], "fid", self.dir, 1024)

        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600,
                         "the file that came back")
        exposed = [(b, a) for b, a in seen if b is None or b & 0o077]
        self.assertEqual(
            exposed, [],
            "the file was readable by group and others before the mode was applied: "
            f"{[(oct(b) if b is not None else None, oct(a)) for b, a in exposed]}")

    def test_a_file_that_appears_in_the_gap_is_not_clobbered(self):
        names = iter(["ab" * 16, "cd" * 16, "ef" * 16])
        victim_name = "dl-" + "ab" * 16 + ".bin"
        victim = os.path.join(self.dir, victim_name)
        with open(victim, "wb") as fh:
            fh.write(b"not ours")

        real_exists = os.path.exists

        def the_writer_sees_the_name_as_free(p):
            if os.path.basename(p) == victim_name:
                return False        # the race, forced: the taken name reads as free
            return real_exists(p)

        with mock.patch.object(secrets, "token_hex", lambda n: next(names)), \
                mock.patch.object(os.path, "exists", the_writer_sees_the_name_as_free):
            try:
                dl.download(lambda fid: {"file_path": "p", "file_size": 4},
                            lambda fp: [b"abcd"], "fid", self.dir, 1024)
            except FileExistsError:
                pass                # the hardened writer refuses a taken name

        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), b"not ours", "a name taken in the gap was reused")


if __name__ == "__main__":
    unittest.main()
