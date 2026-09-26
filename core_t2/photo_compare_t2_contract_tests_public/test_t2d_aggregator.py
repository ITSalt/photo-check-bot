import copy
import itertools
import unittest

from .common import A, parsed, response, sut


class T2DAggregator(unittest.TestCase):
    STATES = [
        ("MATCH", "DIRECT"), ("MATCH", "PARTIAL"),
        ("MISMATCH", "DIRECT"), ("MISMATCH", "PARTIAL"),
        ("MISMATCH", "NONE"),
        ("NOT_VISIBLE_IN_REFERENCE", "PARTIAL"),
        ("NOT_VISIBLE_IN_REFERENCE", "NONE"),
    ]

    def test_49_pair_matrix_and_contradictions(self):
        counts = {}
        for left, right in itertools.product(self.STATES, repeat=2):
            outcome = sut.aggregate_attributes(
                parsed(response(*left))["results"],
                parsed(response(*right))["results"],
            )[A]
            counts[outcome] = counts.get(outcome, 0) + 1
        self.assertEqual(counts, {
            "MATCH": 1, "MISMATCH": 1, "NEEDS_REVIEW": 11,
            "ASYMMETRIC_PASS": 32, "NOT_VISIBLE_IN_REFERENCE": 4,
        })
        for state in [("MATCH", "NONE"),
                      ("NOT_VISIBLE_IN_REFERENCE", "DIRECT")]:
            with self.assertRaises(sut.ContractError):
                parsed(response(*state))

    def test_half_up_padding_and_strict_integer_iou(self):
        self.assertEqual(
            sut.padded_roi([100, 100, 15, 25]), (98, 97, 19, 31)
        )
        base = {
            "candidate_roi": [0, 0, 130, 100],
            "description": "new pocket",
            "visibility_in_reference": "VISIBLE_AND_ABSENT",
        }
        exact = copy.deepcopy(base)
        exact["candidate_roi"] = [70, 0, 130, 100]
        above = copy.deepcopy(base)
        above["candidate_roi"] = [69, 0, 130, 100]
        self.assertEqual(
            sut.aggregate_responses(
                parsed(response(unmapped=[base])),
                parsed(response(unmapped=[exact])),
            )["coverage"],
            "NEEDS_REVIEW",
        )
        self.assertEqual(
            sut.aggregate_responses(
                parsed(response(unmapped=[base])),
                parsed(response(unmapped=[above])),
            )["coverage"],
            "MISMATCH",
        )
