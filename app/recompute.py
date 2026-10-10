"""Offline A / B / C recompute over recorded check codes (WP-CONTROLS-01, D-8, D-10).

    python3 -m app.recompute FILE [FILE ...]

Each FILE is a controls codes file (`vision_controls --codes`) or a bot log: lines holding
`check_codes v=1 a=<STATUS> n=<N> codes=<compact>` are read, any other line is skipped. A
controls line may start with `kind=same|recrop|different`; a controls run without a record
is `kind=<kind> unavailable` (counts against the positive MATCH share).

Rules, recomputed from the codes alone:
  A  the status rule as built (`vision_core.assess`): MISMATCH if any attribute differs or the
     extra element is confirmed; NEEDS_REVIEW if any attribute is to be checked or the extra
     element is unconfirmed; MATCH otherwise. Must equal the recorded `a=` on every line.
  B  coarse PARTIAL+ counts: needs a text/logo/count flag that no data carries yet
     (WP-CONTROLS-02): reported as n/a.
  C  rule A on the first 8 inventory attributes (the extra-element outcome is kept).

Exit 1 when a recorded A differs from the recomputed A (the count is printed) or a
`check_codes` line cannot be parsed. Prints counts and shares only: never a line's content.
"""
import re
import sys

POSITIVE = ("same", "recrop")
KINDS = POSITIVE + ("different",)
RULE_C_ATTRIBUTES = 8
STATUSES = ("MATCH", "MISMATCH", "NEEDS_REVIEW", "UNAVAILABLE")

_LINE = re.compile(r"(?:^|\s)check_codes v=1 a=(MATCH|MISMATCH|NEEDS_REVIEW) n=(\d{1,2}) "
                   r"codes=([0-9A-Za-z,;]{1,160})(?:\s|$)")
_KIND = re.compile(r"(?:^|\s)kind=(same|recrop|different)(?:\s|$)")
_UNAVAILABLE = re.compile(r"^kind=(same|recrop|different) unavailable\s*$")
_ATTR = re.compile(r"^(\d{1,2})([MXNAV])([DPN])([DPN])([01])([CDVOWF])$")
_EXTRA = {"n": "none", "u": "unconfirmed", "c": "confirmed"}


class BadLine(ValueError):
    pass


def parse_codes(n, codes):
    """(list of class letters, extra) from the compact string; BadLine when malformed."""
    body, sep, extra = codes.partition(";")
    if not sep or extra not in _EXTRA:
        raise BadLine
    items = body.split(",") if body else []
    if len(items) != int(n) or not items:
        raise BadLine
    classes = []
    for i, item in enumerate(items, 1):
        m = _ATTR.match(item)
        if not m or int(m.group(1)) != i:
            raise BadLine
        classes.append(m.group(6))
    return classes, _EXTRA[extra]


def rule_a(classes, extra):
    if "D" in classes or extra == "confirmed":
        return "MISMATCH"
    if any(c != "C" for c in classes) or extra == "unconfirmed":
        return "NEEDS_REVIEW"
    return "MATCH"


def rule_c(classes, extra):
    return rule_a(classes[:RULE_C_ATTRIBUTES], extra)


def read_lines(lines):
    """Yield (kind|None, recorded|None|"BAD", classes, extra) per codes line; recorded None
    is a controls run without a record (UNAVAILABLE), "BAD" an unparsable check_codes line."""
    for raw in lines:
        raw = raw.rstrip("\n")
        u = _UNAVAILABLE.match(raw)
        if u:
            yield u.group(1), None, None, None
            continue
        if "check_codes" not in raw:
            continue
        m = _LINE.search(raw)
        if not m:
            yield None, "BAD", None, None
            continue
        k = _KIND.search(raw)
        try:
            classes, extra = parse_codes(m.group(2), m.group(3))
        except BadLine:
            yield None, "BAD", None, None
            continue
        yield (k.group(1) if k else None), m.group(1), classes, extra


def recompute(lines):
    """Counts: {"lines", "bad", "a_diff", "rules": {rule: {kind|None: {status: n}}}}."""
    out = {"lines": 0, "bad": 0, "a_diff": 0,
           "rules": {r: {k: {s: 0 for s in STATUSES} for k in KINDS + (None,)} for r in ("A", "C")}}
    for kind, recorded, classes, extra in read_lines(lines):
        out["lines"] += 1
        if recorded == "BAD":
            out["bad"] += 1
            continue
        if recorded is None:
            for rule in ("A", "C"):
                out["rules"][rule][kind]["UNAVAILABLE"] += 1
            continue
        a = rule_a(classes, extra)
        if a != recorded:
            out["a_diff"] += 1
        out["rules"]["A"][kind][a] += 1
        out["rules"]["C"][kind][rule_c(classes, extra)] += 1
    return out


def _pct(num, den):
    return "n/a" if not den else "%.0f%%" % (100 * num / den)


def report(result):
    """Printable lines: counts and shares only."""
    lines = ["lines=%d" % result["lines"]]
    for rule in ("A", "B", "C"):
        if rule == "B":
            lines.append("rule B: n/a (flag missing)")
            continue
        t = result["rules"][rule]
        pos = {s: sum(t[k][s] for k in POSITIVE) for s in STATUSES}
        pos_n = sum(pos.values())
        coded = {s: sum(t[k][s] for k in KINDS + (None,)) for s in STATUSES}
        coded_n = sum(coded[s] for s in STATUSES if s != "UNAVAILABLE")
        lines.append("rule %s: positive MATCH %s (runs=%d) false MATCH on negatives %d "
                     "NEEDS_REVIEW %s (checks=%d)"
                     % (rule, _pct(pos["MATCH"], pos_n), pos_n, t["different"]["MATCH"],
                        _pct(coded["NEEDS_REVIEW"], coded_n), coded_n))
    lines.append("recorded A differs from recomputed A: %d" % result["a_diff"])
    if result["bad"]:
        lines.append("unparsable check_codes lines: %d" % result["bad"])
    return lines


def _main(argv):
    if not argv:
        print("usage: python3 -m app.recompute FILE [FILE ...]")
        return 2
    lines = []
    for path in argv:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines.extend(f.readlines())
        except OSError:
            print("file unreadable")
            return 2
    result = recompute(lines)
    print(*report(result), sep="\n")
    return 1 if result["a_diff"] or result["bad"] else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
