import photo_compare_t3_candidate as c


FAMILIES = ["COLOR", "GEOMETRY", "MISSING_ELEMENT", "EXTRA_ELEMENT"]
OUTCOMES = ["MATCH", "MISMATCH", "NEEDS_REVIEW", "UNAVAILABLE", "ASYMMETRIC_PASS"]


def manifest():
    rows = []
    for family_index, family in enumerate(FAMILIES):
        for index in range(10):
            truth = "CLEAN" if index < 5 else "DEFECT"
            if truth == "CLEAN":
                outcome = "MISMATCH" if index == 0 else "MATCH"
            else:
                outcome = "MATCH" if index == 5 else ("NEEDS_REVIEW" if index == 6 else "MISMATCH")
            rows.append(
                {
                    "case_id": f"{family}-{index}",
                    "family": family,
                    "truth": truth,
                    "outcome": outcome,
                    "nuisance": ["NONE", "LIGHT", "CROP", "ROTATION"][index % 4],
                    "source_kind": "SYNTHETIC",
                    "planter_id": f"planter-{family_index}-{index}",
                    "grader_id": f"grader-{family_index}-{index}",
                }
            )
    return rows


def test_three_primary_counts_and_raw_ledger():
    rows = manifest()
    result = c.summarize_blind_eval(rows)
    assert result["primary"] == {
        "planted_defect_false_accepts": 4,
        "clean_false_rejects": 4,
        "unresolved": 4,
    }
    assert result["raw_outcomes"] == rows
    assert set(result) == {"primary", "breakdown", "raw_outcomes"}
    assert "accuracy" not in repr(result).lower()


def test_manifest_gates_fail_closed():
    mutations = []
    rows = manifest(); rows.pop(); mutations.append(rows)
    rows = manifest(); rows[0]["case_id"] = rows[1]["case_id"]; mutations.append(rows)
    rows = manifest(); rows[0]["grader_id"] = rows[0]["planter_id"].upper(); mutations.append(rows)
    rows = manifest(); rows[0]["source_kind"] = "UNLABELLED"; mutations.append(rows)
    rows = manifest(); rows[0]["outcome"] = "ACCURATE"; mutations.append(rows)
    rows = manifest(); rows[0]["truth"] = "DEFECT"; mutations.append(rows)
    for bad in mutations:
        try:
            c.summarize_blind_eval(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid manifest was accepted")
