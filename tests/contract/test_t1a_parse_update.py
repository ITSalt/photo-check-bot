"""T1a contract: app/intake/parse.py -> parse_update(update: dict) -> dict | None"""
import unittest

from app.intake import parse as m


def msg(**kw):
    base = {"message_id": 1, "from": {"id": 42}, "chat": {"id": 4242, "type": "private"}}
    base.update(kw)
    return {"update_id": 1, "message": base}


@unittest.skipUnless(getattr(m, "IMPLEMENTED", False), "T1a not implemented yet")
class ParseUpdate(unittest.TestCase):
    def test_largest_photo_size_wins(self):
        u = msg(photo=[{"file_id": "s", "file_size": 100, "width": 90, "height": 90},
                       {"file_id": "L", "file_size": 900, "width": 1280, "height": 1280},
                       {"file_id": "m", "file_size": 400, "width": 320, "height": 320}], caption="ai")
        e = m.parse_update(u)
        self.assertEqual((e["kind"], e["file_id"], e["file_size"], e["caption"]), ("photo", "L", 900, "ai"))
        self.assertEqual((e["user_id"], e["chat_id"]), (42, 4242))

    def test_photo_without_file_size_uses_area(self):
        u = msg(photo=[{"file_id": "a", "width": 90, "height": 90}, {"file_id": "b", "width": 800, "height": 600}])
        self.assertEqual(m.parse_update(u)["file_id"], "b")

    def test_image_document_is_image_doc(self):
        u = msg(document={"file_id": "d", "file_size": 5000, "mime_type": "image/png", "file_name": "x.png"})
        e = m.parse_update(u)
        self.assertEqual((e["kind"], e["file_id"], e["file_size"]), ("image_doc", "d", 5000))

    def test_non_image_document_is_unsupported(self):
        u = msg(document={"file_id": "d", "mime_type": "application/pdf"})
        self.assertEqual(m.parse_update(u)["kind"], "unsupported")

    def test_command_lowercased_and_botname_stripped(self):
        e = m.parse_update(msg(text="/Start@ContractsForPeopleBot landing_ru"))
        self.assertEqual((e["kind"], e["command"], e["arg"]), ("command", "start", "landing_ru"))

    def test_command_without_arg(self):
        e = m.parse_update(msg(text="/cancel"))
        self.assertEqual((e["command"], e["arg"]), ("cancel", None))

    def test_plain_text(self):
        e = m.parse_update(msg(text="привет"))
        self.assertEqual((e["kind"], e["command"], e["file_id"]), ("text", None, None))

    def test_other_message_types_unsupported(self):
        for extra in ({"video": {"file_id": "v"}}, {"sticker": {"file_id": "s"}}, {"voice": {"file_id": "o"}}):
            self.assertEqual(m.parse_update(msg(**extra))["kind"], "unsupported")

    def test_not_a_private_message_is_ignored(self):
        self.assertIsNone(m.parse_update({"update_id": 2, "edited_message": msg(text="x")["message"]}))
        self.assertIsNone(m.parse_update({"update_id": 3, "channel_post": {"text": "x"}}))
        group = msg(text="/start")
        group["message"]["chat"]["type"] = "group"
        self.assertIsNone(m.parse_update(group))

    def test_result_has_all_keys(self):
        e = m.parse_update(msg(text="hi"))
        self.assertEqual(set(e), {"user_id", "chat_id", "kind", "file_id", "file_size", "caption", "command", "arg"})


if __name__ == "__main__":
    unittest.main()
