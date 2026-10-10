"""Smoke-test controls for the status rule (run by the orchestrator, with a key).

    python3 -m app.vision_controls MANIFEST.json [--repeats 3] [--gate] [--codes FILE]

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
A recrop row whose cand equals ref is refused (exit 2): by validate_manifest under --gate,
by a row check in default mode.
--codes FILE writes one line per run: `kind=<kind> check_codes v=1 a=<STATUS> n=<N> codes=<compact>`
(per-attribute codes, WP-CONTROLS-01; or `kind=<kind> unavailable` when the run has no record)
for `python3 -m app.recompute FILE`. No image path, claim, label or model text is written.
After the results it prints the format-reject counts by stage x rule (fixed rule codes from
app.answer_rules, only for the real provider path; none when `compare` is injected).
Prints nothing but counts, kinds and fixed codes: no file paths, bytes or model text.
"""
import json
import logging
import sys

from app import controls_gate
from app.answer_rules import RejectTally

STATUSES = ("MATCH", "MISMATCH", "NEEDS_REVIEW", "UNAVAILABLE")
POSITIVE = ("same", "recrop")
KINDS = POSITIVE + ("different",)
THRESHOLD = 0.90


def run_controls(rows, compare, repeats, read, outcomes=None, record_box=None, on_run=None):
    """tally[kind][status] -> count. `read(path)` returns bytes.

    If `outcomes` is a list, every run is appended to it as (kind, status).
    `record_box` (a list the compare callable's on_codes appends to) and `on_run(kind, status,
    record)` carry the per-attribute code record of each run (None when it has none)."""
    tally = {k: {s: 0 for s in STATUSES} for k in KINDS}
    for row in rows:
        kind = row["kind"]
        if kind not in tally:
            raise ValueError("unknown kind")
        ref = read(row["ref"])
        cand = ref if kind == "same" and not row.get("cand") else read(row["cand"])
        for _ in range(repeats):
            if record_box is not None:
                del record_box[:]
            status = compare(ref, cand).get("status")
            status = status if status in STATUSES else "UNAVAILABLE"
            tally[kind][status] += 1
            if outcomes is not None:
                outcomes.append((kind, status))
            if on_run is not None:
                on_run(kind, status, record_box[-1] if record_box else None)
    return tally


def pct1(share):
    """A share (0..1) as a percentage with one decimal, rounded half up: 64/66 -> 97.0%."""
    tenths = (2000 * share + 1) // 2      # round(1000 * share) half up
    return "%d.%d%%" % (tenths // 10, tenths % 10)


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


def _main(argv, environ, compare=None, read=None, make=None):
    repeats, args = 3, list(argv)
    gate = "--gate" in args
    args = [a for a in args if a != "--gate"]
    codes_path = None
    if "--codes" in args:
        i = args.index("--codes")
        if i + 1 >= len(args):
            print("usage: --codes needs a file")
            return 2
        codes_path = args[i + 1]
        del args[i:i + 2]
    if "--repeats" in args:
        i = args.index("--repeats")
        repeats = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1 or repeats < 1:
        print("usage: python3 -m app.vision_controls MANIFEST.json [--repeats N] [--gate] [--codes FILE]")
        return 2
    rejects = RejectTally()
    record_box = []
    if compare is None:
        if make is None:
            from app.vision_compare import compare_from_env as make
        compare = make(environ, on_reject=rejects.add, on_codes=record_box.append)
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
    if not gate and any(isinstance(r, dict) and r.get("kind") == "recrop" and r.get("cand") in (None, "", r.get("ref"))
           for r in rows):
        print("manifest refused: RECROP_SAME_FILE")
        return 2
    codes_file = None
    on_run = None
    if codes_path is not None:
        try:
            codes_file = open(codes_path, "w", encoding="utf-8")
        except OSError:
            print("codes file unusable")   # never the path
            return 2

        def on_run(kind, status, record):
            if record:
                from app.vision_core import codes_line   # needs core_t2, like the real chain
                codes_file.write("kind=%s %s\n" % (kind, codes_line(record)))
            elif status == "UNAVAILABLE":
                codes_file.write("kind=%s unavailable\n" % kind)
            codes_file.flush()
    outcomes = []
    try:
        tally = run_controls(rows, compare, repeats, read, outcomes, record_box, on_run)
    finally:
        if codes_file is not None:
            codes_file.close()
    if gate:
        verdict = controls_gate.gate(outcomes)
        print(*rejects.lines(), sep="\n")   # before the report: it ends with the verdict line
        print(controls_gate.format_report(verdict))
        return 0 if verdict["pass"] else 1
    result = summarize(tally)
    for kind in KINDS + ("positive",):
        r = result[kind]
        print(kind, "runs=%d" % r["runs"], *("%s=%s" % (s, "n/a" if r[s] is None else pct1(r[s]))
                                              for s in STATUSES))
    print("RULE TOO STRICT (positive MATCH < 90%)" if result["rule_too_strict"] else "positive control ok")
    false_match = tally["different"]["MATCH"]
    print("false MATCH on negatives: %d" % false_match)
    print(*rejects.lines(), sep="\n")
    return 1 if result["rule_too_strict"] or false_match else 0


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    sys.exit(_main(sys.argv[1:], os.environ))
