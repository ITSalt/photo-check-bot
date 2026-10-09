"""Smoke-test controls for the status rule (run by the orchestrator, with a key).

    python3 -m app.vision_controls MANIFEST.json [--repeats 3] [--gate]

MANIFEST is a JSON list of {"kind": "same"|"recrop"|"different", "ref": path, "cand": path}:
  same       positive control: the photo against itself (cand may be omitted)
  recrop     positive control: the photo against a slightly cropped, re-encoded copy
  different  negative control: two different products of one category
Prints per kind the counts and shares of MATCH / MISMATCH / NEEDS_REVIEW / UNAVAILABLE
(UNAVAILABLE counts against the rule, over all runs). Rule: positive MATCH share below
90% means the status rule is too strict. Costs three provider calls per run.
Default mode (pre-demo, 10-20 photos) also prints `false MATCH on negatives: <k>` and exits 1
when k > 0. With --gate the manifest is validated first (app.controls_gate: at least 20
positive and 20 different rows, licence on every different row; invalid -> exit 2 before any
provider call) and the run passes only if positive MATCH >= 90% and negative MISMATCH >= 90%
(UNAVAILABLE counts against both): prints CONTROLS PASS (exit 0) or CONTROLS FAIL (exit 1).
A recrop row whose cand equals ref is refused (exit 2) in both modes.
Prints nothing but counts and kinds: no paths, bytes or model text.
"""
import json
import logging
import sys

from app import controls_gate

STATUSES = ("MATCH", "MISMATCH", "NEEDS_REVIEW", "UNAVAILABLE")
POSITIVE = ("same", "recrop")
KINDS = POSITIVE + ("different",)
THRESHOLD = 0.90


def run_controls(rows, compare, repeats, read, outcomes=None):
    """tally[kind][status] -> count. `read(path)` returns bytes.

    If `outcomes` is a list, every run is appended to it as (kind, status)."""
    tally = {k: {s: 0 for s in STATUSES} for k in KINDS}
    for row in rows:
        kind = row["kind"]
        if kind not in tally:
            raise ValueError("unknown kind")
        ref = read(row["ref"])
        cand = ref if kind == "same" and not row.get("cand") else read(row["cand"])
        for _ in range(repeats):
            status = compare(ref, cand).get("status")
            status = status if status in STATUSES else "UNAVAILABLE"
            tally[kind][status] += 1
            if outcomes is not None:
                outcomes.append((kind, status))
    return tally


def summarize(tally):
    """Shares per kind and pooled positives, plus the rule verdict."""
    def share(counts):
        n = sum(counts.values())
        return {"runs": n, **{s: (counts[s] / n if n else None) for s in STATUSES}}
    out = {k: share(tally[k]) for k in KINDS}
    pooled = {s: sum(tally[k][s] for k in POSITIVE) for s in STATUSES}
    out["positive"] = share(pooled)
    m = out["positive"]["MATCH"]
    out["rule_too_strict"] = (m is not None) and m < THRESHOLD
    return out


def _main(argv, environ, compare=None, read=None):
    repeats, args = 3, list(argv)
    gate = "--gate" in args
    args = [a for a in args if a != "--gate"]
    if "--repeats" in args:
        i = args.index("--repeats")
        repeats = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1 or repeats < 1:
        print("usage: python3 -m app.vision_controls MANIFEST.json [--repeats N] [--gate]")
        return 2
    if compare is None:
        from app.vision_compare import compare_from_env
        compare = compare_from_env(environ)
        if compare is None:
            print("vision not configured")
            return 2
    read = read or (lambda p: open(p, "rb").read())
    with open(args[0], encoding="utf-8") as f:
        rows = json.load(f)
    if gate:
        try:
            controls_gate.validate_manifest(rows)
        except controls_gate.ManifestError as e:
            print("manifest refused: %s" % e)  # row index and fixed code only
            return 2
    if any(isinstance(r, dict) and r.get("kind") == "recrop" and r.get("cand") in (None, "", r.get("ref"))
           for r in rows):
        print("manifest refused: RECROP_SAME_FILE")
        return 2
    outcomes = []
    tally = run_controls(rows, compare, repeats, read, outcomes)
    if gate:
        verdict = controls_gate.gate(outcomes)
        print(controls_gate.format_report(verdict))
        return 0 if verdict["pass"] else 1
    result = summarize(tally)
    for kind in KINDS + ("positive",):
        r = result[kind]
        print(kind, "runs=%d" % r["runs"], *("%s=%s" % (s, "n/a" if r[s] is None else "%.0f%%" % (100 * r[s]))
                                              for s in STATUSES))
    print("RULE TOO STRICT (positive MATCH < 90%)" if result["rule_too_strict"] else "positive control ok")
    false_match = tally["different"]["MATCH"]
    print("false MATCH on negatives: %d" % false_match)
    return 1 if result["rule_too_strict"] or false_match else 0


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    sys.exit(_main(sys.argv[1:], os.environ))
