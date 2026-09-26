import photo_compare_t2_candidate as sut

R, C, A = "reference-1", "candidate-1", "button.count"


def inventory():
    return {
        "schema_version": "photo-inventory/1",
        "reference_photo_id": R,
        "attributes": [{
            "attribute_id": A,
            "claim": "Two buttons are visible.",
            "reference_roi": [100, 100, 300, 400],
        }],
    }


def response(verdict="MATCH", evidence="DIRECT", unmapped=None):
    return {
        "schema_version": "photo-compare/2",
        "inventory_sha256": sut.sha256_hex(inventory()),
        "results": [{
            "attribute_id": A,
            "verdict": verdict,
            "evidence_quality": evidence,
            "reference_roi": (
                None if verdict == "NOT_VISIBLE_IN_REFERENCE"
                else [100, 100, 300, 400]
            ),
            "candidate_roi": [120, 90, 300, 400],
            "observation": "Two buttons are visible in both images.",
        }],
        "unmapped_candidate_elements": [] if unmapped is None else unmapped,
    }


def parsed(payload):
    return sut.validate_response(
        payload, {A}, expected_inventory_sha256=sut.sha256_hex(inventory())
    )
