"""Studio session_factory to the WP-BOT-02 / T3a contract (D-13). Standard library only.

    make_session_factory(api_get_file, api_fetch, dest_dir, max_bytes)
        -> session_factory(reference_file_id, candidate_file_id) -> Session

Each check gets a fresh private directory (0o700) inside `dest_dir`; both photos are
streamed into it by app/intake/download.py (random names, 0o600, size cap enforced on
the declared and on the streamed size). If either download fails, everything already
written is removed and the exception is re-raised. Session.delete_all() removes both
files and the directory and raises if it cannot (T3a: no verdict then).

Photos are deleted when the file system allows it; a failure is never silent: a failed
cleanup on the failure path logs the fixed code `session_cleanup_incomplete` and leaves the
directory for prepare_root's sweep at the next startup. That fixed code is the only thing
logged here: no file id, path, errno text or bytes can reach a log line. Only files inside
the session's own directory are ever deleted.
"""
import errno
import logging
import os
import shutil
import tempfile

from app.intake.download import download

SESSION_PREFIX = "chk-"
log = logging.getLogger("session_factory")


class Session:
    def __init__(self, directory, reference_path, candidate_path):
        self.directory = directory
        self.reference_path, self.candidate_path = reference_path, candidate_path

    def photos(self):
        with open(self.reference_path, "rb") as f:
            reference = f.read()
        with open(self.candidate_path, "rb") as f:
            candidate = f.read()
        return reference, candidate

    def delete_all(self):
        """Remove both photos and the session directory; raise if anything stays.

        Every file and then the directory is attempted whatever failed before. The error
        carries the errno and a fixed message, never a path (the directory name is random
        and a traceback must not show it). A retry after the cause is gone cleans up.
        """
        failed = None
        for path in (self.reference_path, self.candidate_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                failed = failed or exc.errno
        try:
            if os.path.isdir(self.directory):
                shutil.rmtree(self.directory)
        except OSError as exc:
            failed = failed or exc.errno
        if failed is not None or os.path.exists(self.directory):
            raise OSError(failed, "could not delete a session photo") from None


def _remove_tree(directory):
    # Best effort on the failure path: the original exception is the one that leaves.
    try:
        shutil.rmtree(directory)
    except OSError:
        # Fixed code only: no path, no errno text. prepare_root sweeps it at startup.
        log.warning("session_cleanup_incomplete")


def make_session_factory(api_get_file, api_fetch, dest_dir, max_bytes):
    if not callable(api_get_file) or not callable(api_fetch):
        raise ValueError("INVALID_DOWNLOAD_DEPENDENCY")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("INVALID_MAX_BYTES")
    root = os.path.abspath(os.fspath(dest_dir))

    def session_factory(reference_file_id, candidate_file_id):
        directory = os.path.abspath(tempfile.mkdtemp(prefix=SESSION_PREFIX, dir=root))   # 0o700
        try:
            reference = download(api_get_file, api_fetch, reference_file_id, directory, max_bytes)
            candidate = download(api_get_file, api_fetch, candidate_file_id, directory, max_bytes)
            for path in (reference, candidate):
                if os.path.dirname(os.path.abspath(path)) != directory:
                    # Defence in depth: whatever download returned must sit in our directory.
                    # Never unlink it: a path outside the session is not ours to delete.
                    raise OSError(errno.EINVAL, "download returned a path outside the session")
        except BaseException:
            _remove_tree(directory)
            raise
        return Session(directory, reference, candidate)

    return session_factory


def prepare_root(dest_dir):
    """Create the photo root (0o700) and remove anything a crashed run left there."""
    root = os.path.abspath(os.fspath(dest_dir))
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    for entry in os.scandir(root):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.remove(entry.path)
    return root
