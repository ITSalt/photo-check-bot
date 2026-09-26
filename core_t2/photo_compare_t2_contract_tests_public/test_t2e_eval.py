import copy
import unittest

from .common import sut


class T2EEval(unittest.TestCase):
    def test_m1_live_guard_and_counterfactual_are_separate(self):
        rows = [{
            "case_id": "m1", "family": "mapping-drift",
            "kind": "M1_MAPPING_DRIFT",
            "outcome": "REJECTED_PRE_PROVIDER",
            "guard_outcome": "REJECTED_PRE_PROVIDER",
            "provider_called": False,
            "counterfactual_falsifier": "SAME_AS_BASELINE_IS_FAILURE",
            "planter": "alice", "grader": "bob", "as_of": 1790430000,
        }]
        result = sut.summarize_eval(rows)
        self.assertEqual(result["raw_outcomes"], rows)
        self.assertEqual(
            result["counts"]["mapping-drift"]["REJECTED_PRE_PROVIDER"], 1
        )
        for field, value in [
            ("provider_called", True),
            ("counterfactual_falsifier", "SAME_AS_BASELINE_IS_SUCCESS"),
        ]:
            bad = copy.deepcopy(rows)
            bad[0][field] = value
            with self.assertRaises(sut.ContractError):
                sut.summarize_eval(bad)
