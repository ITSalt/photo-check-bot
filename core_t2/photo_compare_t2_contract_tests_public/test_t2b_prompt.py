import unittest

from .common import C, R, inventory, sut


class T2BPrompt(unittest.TestCase):
    def test_swap_positions_not_roles(self):
        source = inventory()
        digest = sut.sha256_hex(source)
        first, second = sut.build_ordered_calls(
            inventory=source, inventory_sha256=digest,
            reference_photo_id=R, candidate_photo_id=C,
        )
        self.assertEqual(
            [(x["photo_id"], x["role"]) for x in first["images"]],
            [(R, "reference"), (C, "candidate")],
        )
        self.assertEqual(
            [(x["photo_id"], x["role"]) for x in second["images"]],
            [(C, "candidate"), (R, "reference")],
        )
        self.assertEqual(first["inventory"], second["inventory"])
        self.assertEqual(first["inventory_sha256"], second["inventory_sha256"])
