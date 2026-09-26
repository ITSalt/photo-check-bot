import unittest

from .common import R, inventory, sut


class T2AInventory(unittest.TestCase):
    def test_digest_is_pinned_and_mapping_drift_rejects(self):
        baseline = sut.sha256_hex(inventory())
        self.assertEqual(
            sut.validate_inventory(
                inventory(), expected_reference_photo_id=R,
                expected_sha256=baseline,
            )["sha256"],
            baseline,
        )
        shifted = inventory()
        shifted["attributes"][0]["reference_roi"] = [500, 100, 300, 400]
        with self.assertRaises(sut.ContractError):
            sut.validate_inventory(
                shifted, expected_reference_photo_id=R,
                expected_sha256=baseline,
            )

    def test_geometry_and_unknown_fields_reject(self):
        bad = inventory()
        bad["attributes"][0]["reference_roi"] = [900, 100, 300, 400]
        with self.assertRaises(sut.ContractError):
            sut.validate_inventory(bad, expected_reference_photo_id=R)
        bad = inventory()
        bad["attributes"][0]["confidence"] = 1
        with self.assertRaises(sut.ContractError):
            sut.validate_inventory(bad, expected_reference_photo_id=R)
