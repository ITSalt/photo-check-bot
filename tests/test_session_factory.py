"""WP-BOT-02 contract, built in WP-BOT-05 (D-13): fake getFile/fetch only, no network."""
import logging
import os
import stat
import tempfile
import unittest

from app.intake.download import TooLarge
from app.photo_checks import MESSAGES, PhotoChecks
from app.session_factory import make_session_factory, prepare_root

FILES = {"ref-id": b"\xff\xd8\xff\xe0reference-bytes", "cand-id": b"\x89PNG\r\n\x1a\ncandidate-bytes"}


class FakeTelegram:
    def __init__(self, files=FILES, declared=None, fail_on=None):
        self.files, self.declared, self.fail_on = files, declared or {}, fail_on
        self.get_calls, self.fetch_calls = [], []

    def get_file(self, file_id):
        self.get_calls.append(file_id)
        return {"file_id": file_id, "file_path": "photos/" + file_id,
                "file_size": self.declared.get(file_id, len(self.files[file_id]))}

    def fetch(self, file_path):
        self.fetch_calls.append(file_path)
        file_id = file_path.split("/", 1)[1]
        data = self.files[file_id]
        yield data[:5]
        if self.fail_on == file_id:
            raise OSError("broken stream " + file_id)
        yield data[5:]


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class SessionFactoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def factory(self, telegram, max_bytes=1024):
        return make_session_factory(telegram.get_file, telegram.fetch, self.root, max_bytes)

    def test_success_paths_owner_only_and_bytes_in_role_order(self):
        session = self.factory(FakeTelegram())("ref-id", "cand-id")
        for path in (session.reference_path, session.candidate_path):
            self.assertTrue(os.path.isabs(path))
            self.assertEqual(mode(path), 0o600)
            self.assertNotIn("ref-id", path)
            self.assertNotIn("cand-id", path)
        self.assertEqual(mode(session.directory), 0o700)
        self.assertEqual(os.path.dirname(session.directory), os.path.abspath(self.root))
        self.assertEqual(session.photos(), (FILES["ref-id"], FILES["cand-id"]))
        session.delete_all()
        self.assertEqual(os.listdir(self.root), [])

    def test_failure_of_second_download_leaves_dest_empty_and_reraises(self):
        with self.assertRaises(OSError):
            self.factory(FakeTelegram(fail_on="cand-id"))("ref-id", "cand-id")
        self.assertEqual(os.listdir(self.root), [])

    def test_failure_of_first_download_leaves_dest_empty(self):
        telegram = FakeTelegram(fail_on="ref-id")
        with self.assertRaises(OSError):
            self.factory(telegram)("ref-id", "cand-id")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(telegram.get_calls, ["ref-id"])

    def test_oversize_declared_never_calls_fetch(self):
        telegram = FakeTelegram(declared={"ref-id": 10 ** 9})
        with self.assertRaises(TooLarge):
            self.factory(telegram)("ref-id", "cand-id")
        self.assertEqual(telegram.fetch_calls, [])
        self.assertEqual(os.listdir(self.root), [])

    def test_streamed_over_cap_second_photo_cleans_everything(self):
        telegram = FakeTelegram(declared={"cand-id": None})
        with self.assertRaises(TooLarge):
            self.factory(telegram, max_bytes=len(FILES["ref-id"]))("ref-id", "cand-id")
        self.assertEqual(os.listdir(self.root), [])

    def test_delete_all_is_idempotent_and_empties_dest(self):
        session = self.factory(FakeTelegram())("ref-id", "cand-id")
        session.delete_all()
        session.delete_all()
        self.assertEqual(os.listdir(self.root), [])

    def test_no_log_lines_at_all(self):
        with self.assertNoLogs(level=logging.DEBUG):
            self.factory(FakeTelegram())("ref-id", "cand-id").delete_all()
            with self.assertRaises(OSError):
                self.factory(FakeTelegram(fail_on="cand-id"))("ref-id", "cand-id")

    def test_bad_dependencies_rejected(self):
        with self.assertRaises(ValueError):
            make_session_factory(None, FakeTelegram().fetch, self.root, 10)
        with self.assertRaises(ValueError):
            make_session_factory(FakeTelegram().get_file, FakeTelegram().fetch, self.root, 0)

    def test_prepare_root_removes_leftovers_and_is_private(self):
        root = os.path.join(self.root, "photos")
        os.makedirs(os.path.join(root, "chk-stale"))
        with open(os.path.join(root, "chk-stale", "dl-x.bin"), "wb") as f:
            f.write(b"old")
        with open(os.path.join(root, "loose.bin"), "wb") as f:
            f.write(b"old")
        prepare_root(root)
        self.assertEqual(os.listdir(root), [])
        self.assertEqual(mode(root), 0o700)


class RealFactoryInT3aWiring(unittest.TestCase):
    """tests/test_t3a_wiring.py scenarios with the real factory in place of the Mock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        files = {"generated-a": b"GEN-A", "real-a": b"REAL-A", "generated-b": b"GEN-B", "real-b": b"REAL-B"}
        self.telegram = FakeTelegram(files=files)
        self.seen = []
        def compare(reference, candidate):
            self.seen.append((reference, candidate, len(os.listdir(self.tmp.name))))
            return {"status": "MATCH"}
        factory = make_session_factory(self.telegram.get_file, self.telegram.fetch, self.tmp.name, 1024)
        self.checks = PhotoChecks(factory, compare)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, fid, user, caption=None):
        return {"user_id": user, "chat_id": user, "kind": "photo", "file_id": fid, "caption": caption}

    def test_interleaved_users_keep_roles_and_clean_up(self):
        self.checks.handle(self.event("generated-a", 1))
        self.checks.handle(self.event("real-b", 2, "real"))
        self.assertEqual(self.checks.handle(self.event("real-a", 1)), [MESSAGES["MATCH"]])
        self.assertEqual(self.checks.handle(self.event("generated-b", 2)), [MESSAGES["MATCH"]])
        self.assertEqual(self.seen, [(b"REAL-A", b"GEN-A", 1), (b"REAL-B", b"GEN-B", 1)])
        self.assertEqual(os.listdir(self.tmp.name), [])
        self.assertEqual(self.checks.pending, {})

    def test_download_failure_is_unavailable_without_compare(self):
        self.telegram.fail_on = "real-a"
        self.checks.handle(self.event("generated-a", 1))
        self.assertEqual(self.checks.handle(self.event("real-a", 1)), [MESSAGES["UNAVAILABLE"]])
        self.assertEqual(self.seen, [])
        self.assertEqual(os.listdir(self.tmp.name), [])


if __name__ == "__main__":
    unittest.main()
