"""Offline A / B / C recompute over recorded check codes (WP-CONTROLS-01/02, D-8, D-10).

    python3 -m app.recompute FILE [FILE ...]

Each FILE is a controls codes file (`vision_controls --codes`) or a bot log: lines holding
`check_codes v=2 a=<STATUS> n=<N> codes=<compact>` (or the older `v=1`) are read, any other line
is skipped. A controls line may start with `kind=same|recrop|different`; a controls run without
a record is `kind=<kind> unavailable` (counts against the positive MATCH share). A
`check_codes_error` line (a record that could not be written) is counted separately, not as
an unparsable codes line.

Rules, recomputed from the codes alone:
  A  the status rule as built (`vision_core.assess`): MISMATCH if any attribute differs or the
     extra element is confirmed; NEEDS_REVIEW if any attribute is to be checked or the extra
     element is unconfirmed; MATCH otherwise. Must equal the recorded `a=` on every line.
  B  rule A where a coarse attribute (not fine by the marker, not text/logo/count by the v=2
     flag) also counts as confirmed when both orders said MATCH and the evidence of both is
     PARTIAL or better (D-10). v=1 lines carry no flag: B is n/a for them (counted, left out).
  C  rule A on the first 8 inventory attributes (the extra-element outcome is kept).
  B and C keep the extra-element outcome the same way.

Exit 1 when a recorded A differs from the recomputed A, a `check_codes` line cannot be parsed
or a class letter contradicts its outcome/evidence/fine (the counts are printed). Prints counts
and one-decimal shares only (half up): never a line's content.
"""
import re
import sys

POSITIVE = ("same", "recrop")
KINDS = POSITIVE + ("different",)
RULE_C_ATTRIBUTES = 8
STATUSES = ("MATCH", "MISMATCH", "NEEDS_REVIEW", "UNAVAILABLE")
RULES = ("A", "B", "C")

_LINE = re.compile(r"(?:^|\s)check_codes v=([12]) a=(MATCH|MISMATCH|NEEDS_REVIEW) n=(\d{1,2}) "
                   r"codes=([0-9A-Za-z,;]{1,160})(?:\s|$)")
_ERROR = re.compile(r"(?:^|\s)check_codes_error(?:\s|$)")
_KIND = re.compile(r"(?:^|\s)kind=(same|recrop|different)(?:\s|$)")
_UNAVAILABLE = re.compile(r"^kind=(same|recrop|different) unavailable\s*$")
_ATTR_V1 = re.compile(r"^(\d{1,2})([MXNAV])([DPN])([DPN])([01])([CDVOWF])$")
_ATTR_V2 = re.compile(r"^(\d{1,2})([MXNAV])([DPN])([DPN])([01])([01])([01])([CDVOWF])$")
_EXTRA = {"n": "none", "u": "unconfirmed", "c": "confirmed"}


class BadLine(ValueError):
    pass


def parse_attrs(version, n, codes):
    """(list of attribute dicts, extra) from the compact string; BadLine when malformed.

    An attribute is {"outcome", "ev": (e1, e2), "fine": bool, "tlc": bool|None,
    "both_match": bool|None, "cls"} (tlc / both_match are None on v=1 lines)."""
    body, sep, extra = codes.partition(";")
    if not sep or extra not in _EXTRA:
        raise BadLine
    items = body.split(",") if body else []
    if len(items) != int(n) or not items:
        raise BadLine
    attrs = []
    for i, item in enumerate(items, 1):
        m = (_ATTR_V2 if version == "2" else _ATTR_V1).match(item)
        if not m or int(m.group(1)) != i:
            raise BadLine
        g = m.groups()
        attrs.append({"outcome": g[1], "ev": (g[2], g[3]), "fine": g[4] == "1",
                      "tlc": g[5] == "1" if version == "2" else None,
                      "both_match": g[6] == "1" if version == "2" else None, "cls": g[-1]})
    return attrs, _EXTRA[extra]


def parse_codes(n, codes, version="1"):
    """(list of class letters, extra) from the compact string; BadLine when malformed."""
    attrs, extra = parse_attrs(version, n, codes)
    return [a["cls"] for a in attrs], extra


def consistent(a):
    """The class letter agrees with outcome / evidence / fine (the mapping `_classify` applies)."""
    out, ev, cls = a["outcome"], a["ev"], a["cls"]
    if out == "M":
        return cls == "C" and ev == ("D", "D")
    if out == "X":
        return cls == "D" and ev == ("D", "D")
    if out == "V" or "N" in ev:
        return cls == "V"
    if a["fine"]:
        return cls == "F"
    return cls == ("O" if out == "A" else "W")


def rule_a(classes, extra):
    if "D" in classes or extra == "confirmed":
        return "MISMATCH"
    if any(c != "C" for c in classes) or extra == "unconfirmed":
        return "NEEDS_REVIEW"
    return "MATCH"


def rule_b(attrs, extra):
    """Rule A where a coarse attribute (not fine, not text/logo/count) with MATCH in both orders
    and evidence PARTIAL or better also counts as confirmed (D-10). Needs v=2 attributes."""
    classes = []
    for a in attrs:
        coarse = not a["fine"] and not a["tlc"]
        if coarse and a["both_match"] and "N" not in a["ev"]:
            classes.append("C")
        else:
            classes.append(a["cls"])
    return rule_a(classes, extra)


def rule_c(classes, extra):
    return rule_a(classes[:RULE_C_ATTRIBUTES], extra)


def read_lines(lines):
    """Yield (kind|None, recorded|None|"BAD"|"ERR", classes, extra, attrs, version) per codes line;
    recorded None is a controls run without a record (UNAVAILABLE), "BAD" an unparsable
    check_codes line, "ERR" a check_codes_error line."""
    for raw in lines:
        raw = raw.rstrip("\n")
        u = _UNAVAILABLE.match(raw)
        if u:
            yield u.group(1), None, None, None, None, None
            continue
        if "check_codes" not in raw:
            continue
        if _ERROR.search(raw):
            yield None, "ERR", None, None, None, None
            continue
        m = _LINE.search(raw)
        if not m:
            yield None, "BAD", None, None, None, None
            continue
        k = _KIND.search(raw)
        try:
            attrs, extra = parse_attrs(m.group(1), m.group(3), m.group(4))
        except BadLine:
            yield None, "BAD", None, None, None, None
            continue
        yield ((k.group(1) if k else None), m.group(2), [a["cls"] for a in attrs], extra, attrs,
               m.group(1))


def recompute(lines):
    """Counts: {"lines", "bad", "errors", "inconsistent", "a_diff", "v1", "rules": {rule: {kind|None:
    {status: n}}}}. `lines` counts codes lines and unavailable runs, not `check_codes_error`;
    `v1` counts v=1 lines, left out of rule B."""
    out = {"lines": 0, "bad": 0, "errors": 0, "inconsistent": 0, "a_diff": 0, "v1": 0,
           "rules": {r: {k: {s: 0 for s in STATUSES} for k in KINDS + (None,)} for r in RULES}}
    for kind, recorded, classes, extra, attrs, version in read_lines(lines):
        if recorded == "ERR":
            out["errors"] += 1
            continue
        out["lines"] += 1
        if recorded == "BAD":
            out["bad"] += 1
            continue
        if recorded is None:
            for rule in RULES:
                out["rules"][rule][kind]["UNAVAILABLE"] += 1
            continue
        if not all(consistent(a) for a in attrs):
            out["inconsistent"] += 1
        a = rule_a(classes, extra)
        if a != recorded:
            out["a_diff"] += 1
        out["rules"]["A"][kind][a] += 1
        out["rules"]["C"][kind][rule_c(classes, extra)] += 1
        if version == "2":
            out["rules"]["B"][kind][rule_b(attrs, extra)] += 1
        else:
            out["v1"] += 1
    return out


def _pct(num, den):
    """num/den as a percentage with one decimal, rounded half up (exact integer arithmetic)."""
    if not den:
        return "n/a"
    tenths = (2000 * num + den) // (2 * den)
    return "%d.%d%%" % (tenths // 10, tenths % 10)


def report(result):
    """Printable lines: counts and shares only."""
    lines = ["lines=%d" % result["lines"]]
    for rule in RULES:
        t = result["rules"][rule]
        pos = {s: sum(t[k][s] for k in POSITIVE) for s in STATUSES}
        pos_n = sum(pos.values())
        coded = {s: sum(t[k][s] for k in KINDS + (None,)) for s in STATUSES}
        coded_n = sum(coded[s] for s in STATUSES if s != "UNAVAILABLE")
        if rule == "B" and not coded_n:
            lines.append("rule B: n/a (no check_codes v=2 lines)")
            continue
        lines.append("rule %s: positive MATCH %s (runs=%d) false MATCH on negatives %d "
                     "NEEDS_REVIEW %s (checks=%d)"
                     % (rule, _pct(pos["MATCH"], pos_n), pos_n, t["different"]["MATCH"],
                        _pct(coded["NEEDS_REVIEW"], coded_n), coded_n))
    if result["v1"]:
        lines.append("check_codes v=1 lines left out of rule B (no flag): %d" % result["v1"])
    lines.append("recorded A differs from recomputed A: %d" % result["a_diff"])
    if result["bad"]:
        lines.append("unparsable check_codes lines: %d" % result["bad"])
    if result["inconsistent"]:
        lines.append("check_codes lines whose class contradicts outcome/evidence/fine: %d"
                     % result["inconsistent"])
    if result["errors"]:
        lines.append("check_codes_error lines (record not written, skipped): %d" % result["errors"])
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
    return 1 if result["a_diff"] or result["bad"] or result["inconsistent"] else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
