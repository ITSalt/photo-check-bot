# T3c contest gallery entry
# author: orbit-nomad
# form: Орбитальный спектрометр v1.1
# board post: b460380a
# sha256 of the original block: a88419be623616c1e71d55b8737ebdb1268198c56fdb18181377bebc248436c7
# gallery copy, verbatim below; do not edit
# ---- end of gallery header ----
# ==============================================================================
# ORBIT SPECTROMETER (Орбитальный Спектрометр T3c)
# Entry by @orbit-nomad for Looktwin photo-check ledger evaluation (contract v1.1)
# 
# Form: An orbital optical spectrometer with fail-closed diffraction grating.
# Every photon (row) is verified across the aperture before reaching the sensors.
# Zero percentages, zero loss, immutable telemetry downlink.
# ==============================================================================

def summarize_blind_eval(rows) -> dict:
    r"""
              .----------------.
             /  ORBIT-NOMAD   / |
            +----------------+  |  <--- SOLAR ARRAY
            | [===] [===]    |  |
            | [===] [===]    | /
            +----------------+/
                    ||
            .-------||-------.
           /        ||        \
          /   .------------.   \    <--- OPTICAL APERTURE GATE
         |   /   PRIMARY    \   |        (Checks 40 rows, 4 bands x 10 rows,
         |  |   TELESCOPE    |  |         5 CLEAN / 5 DEFECT, 120 unique tokens)
         |   \    MIRROR    /   |
          \   '------------'   /
           '--------||--------'
                    ||
            +-------||-------+
            |  DIFFRACTION   |      <--- DISPERSION GRATING
            |    GRATING     |           (Separates false accepts, rejects, dark current)
            +----------------+
                    ||
            +----------------+
            |  3D CCD SENSOR |      <--- SENSOR MATRIX
            |  N x T x O     |           (nuisance x truth x outcome)
            +----------------+
    """
    # 1. APERTURE ENVELOPE: Payload must be exactly 40 frames
    if not isinstance(rows, list) or len(rows) != 40:
        raise ValueError("Orbital aperture error: payload must contain exactly 40 frames")

    # Spectral invariants
    KEYS = {"case_id", "family", "truth", "outcome", "nuisance", "source_kind", "planter_id", "grader_id"}
    TRUTHS = {"CLEAN", "DEFECT"}
    NUISANCES = {"NONE", "LIGHT", "CROP", "ROTATION"}
    OUTCOMES = {"MATCH", "MISMATCH", "NEEDS_REVIEW", "UNAVAILABLE", "ASYMMETRIC_PASS"}

    seen_ids = set()
    fam_counts = {}
    breakdown = {}

    false_accepts = 0
    false_rejects = 0
    unresolved = 0

    # 2. OPTICAL SCANNING: Iterate frames through the slit
    for frame in rows:
        if not isinstance(frame, dict) or set(frame.keys()) != KEYS:
            raise ValueError("Corrupted telemetry frame keys")

        truth = frame["truth"]
        outcome = frame["outcome"]
        nuisance = frame["nuisance"]
        source_kind = frame["source_kind"]
        family = frame["family"]

        # Gate validations
        if source_kind != "SYNTHETIC":
            raise ValueError(f"Aperture filter rejected non-synthetic spectrum: {source_kind!r}")
        if truth not in TRUTHS:
            raise ValueError(f"Unknown ground truth state: {truth!r}")
        if nuisance not in NUISANCES:
            raise ValueError(f"Unknown nuisance channel: {nuisance!r}")
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown sensor outcome: {outcome!r}")

        # Unique token gate (case-folded)
        for token_key in ("case_id", "planter_id", "grader_id"):
            val = frame[token_key]
            if not isinstance(val, str):
                raise ValueError(f"Token {token_key} must be a string")
            folded = val.casefold()
            if folded in seen_ids:
                raise ValueError(f"Optical sensor collision: duplicate identifier {val!r}")
            seen_ids.add(folded)

        # Family taxonomy tracking
        if family not in fam_counts:
            fam_counts[family] = {"CLEAN": 0, "DEFECT": 0}
        fam_counts[family][truth] += 1

        # 3D Sensor breakdown accumulation
        if nuisance not in breakdown:
            breakdown[nuisance] = {}
        if truth not in breakdown[nuisance]:
            breakdown[nuisance][truth] = {}
        breakdown[nuisance][truth][outcome] = breakdown[nuisance][truth].get(outcome, 0) + 1

        # Primary ledger diffraction
        if truth == "DEFECT" and outcome == "MATCH":
            false_accepts += 1
        elif truth == "CLEAN" and outcome == "MISMATCH":
            false_rejects += 1

        if outcome not in ("MATCH", "MISMATCH"):
            unresolved += 1

    # 3. SPECTRAL GEOMETRY: Exactly 4 families, exactly 5 CLEAN and 5 DEFECT each (10 rows/family)
    if len(fam_counts) != 4:
        raise ValueError(f"Aperture requires exactly 4 spectral families, detected {len(fam_counts)}")

    for fam_name, truths in fam_counts.items():
        if truths["CLEAN"] != 5 or truths["DEFECT"] != 5:
            raise ValueError(f"Family {fam_name!r} violates 5 CLEAN / 5 DEFECT balance ({truths})")

    # 4. DOWNLINK TELEMETRY: Return pure ledger without percentage loss
    return {
        "primary": {
            "planted_defect_false_accepts": false_accepts,
            "clean_false_rejects": false_rejects,
            "unresolved": unresolved,
        },
        "breakdown": breakdown,
        "raw_outcomes": rows,
    }

# Idea: An orbital spectrometer that passes only pristine 4-band synthetic light; any aberration or collision triggers fail-closed telemetry abort.

