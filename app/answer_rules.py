"""Safe diagnostics for format rejects of a model answer (WP-BOT-15).

A reject is classified into a code from the closed list RULES, plus an optional path built
only from OUR field names and integer indices. The T2 `ContractError` message is never
logged or printed: it embeds model-provided values (`%r` of rois, verdicts, attribute ids,
unknown keys). Classification matches the fixed templates written in core_t2 (prefix and
suffix patterns here); whatever does not match is OTHER. Model values never reach the
returned tuple: FIELDS_UNKNOWN carries a count, FIELDS_MISSING our own field names.
"""
import ast
import re

# rule -> meaning (docs/t4-vision.md lists the same table)
RULES = {
    "WRAPPER_KEYS": "inventory answer keys are not exactly {attributes}",
    "NO_ATTRIBUTES": "attributes is not a list, or it is empty",
    "TOO_MANY_ATTRIBUTES": "more attributes than MAX_ATTRIBUTES",
    "NOT_OBJECT": "an object was expected (attribute, result, element, response)",
    "SCHEMA_VERSION": "schema_version is not the expected one",
    "REF_ID": "reference_photo_id is not the expected one",
    "FIELDS_MISSING": "a required field is missing (our field names printed)",
    "FIELDS_UNKNOWN": "an unknown field is present (count only, never the name)",
    "TEXT_LENGTH": "a text field is not a string, blank, or too long",
    "SHA_FORMAT": "a sha256 field is not a lowercase hex digest",
    "ROI_SHAPE": "a roi is not four integers",
    "ROI_OUT_OF_FRAME": "a roi is outside the frame or smaller than 100",
    "DUP_ATTR": "duplicate attribute_id in the inventory",
    "SHA_MISMATCH": "inventory_sha256 is not the pinned inventory",
    "RESULTS_NOT_LIST": "results or unmapped_candidate_elements is not a list",
    "ATTR_UNKNOWN_OR_DUP": "a result names an unknown or repeated attribute_id",
    "VERDICT_EVIDENCE": "verdict or evidence_quality invalid, or contradictory together",
    "ROI_NOT_NULL": "reference_roi must be null for NOT_VISIBLE_IN_REFERENCE",
    "RESULTS_MISSING": "an inventory attribute has no result",
    "VISIBILITY_VALUE": "visibility_in_reference is not an allowed value",
    "NOT_CANONICAL": "the answer is not canonical JSON (NaN, Infinity, odd types)",
    "EXPECTED_IDS": "internal: no expected attribute ids",
    "OTHER": "no known template matched",
}

# Repairs applied to an answer that is then validated as usual (not rejects).
FIXES = ("ROI_CLAMPED", "TRAILING_TAIL")

# Field names of our contract: the only identifiers a path or a missing list may carry.
_FIELDS = frozenset({
    "attributes", "results", "unmapped_candidate_elements", "attribute_id", "claim",
    "reference_roi", "candidate_roi", "observation", "description", "verdict",
    "evidence_quality", "visibility_in_reference", "reference_photo_id", "schema_version",
    "inventory_sha256"})
_ROOTS = ("inventory", "response")
_PATH = r"[a-z_][a-z_0-9]*(?:\.[a-z_][a-z_0-9]*|\[\d{1,4}\])*"
_HEAD = re.compile(r"(%s): " % _PATH)
_MISSING_TAIL = re.compile(r", missing \[((?:'[a-z_0-9]+'(?:, )?)*)\]$")
_ROI_REPR = re.compile(r"\[-?\d+(?:, -?\d+){3}\] is outside the frame or smaller than 100")
_SAFE_PATH = re.compile(_PATH + r"\Z")
_SAFE_MISSING = re.compile(r"[a-z_0-9]+(?:,[a-z_0-9]+)*\Z")


def _clean_path(raw):
    """Our own path (root stripped), or None if any token is not one of our names."""
    parts = raw.split(".")
    if parts[0] not in _ROOTS:
        return None
    rest = parts[1:]
    for token in rest:
        if token.split("[", 1)[0] not in _FIELDS:
            return None
    # a bracket directly on the root ("inventory.attributes[3]") is covered by the loop;
    # the root itself carries no index
    return ".".join(rest) or None


def _fields_rule(rest):
    """`unknown [...], missing [...]` -> (rule, detail). Model key names are never kept."""
    tail = _MISSING_TAIL.search(rest)
    if not tail:
        return "OTHER", {}
    missing = [m for m in re.findall(r"'([a-z_0-9]+)'", tail.group(1)) if m in _FIELDS]
    head = rest[len("unknown "):tail.start()]
    try:
        unknown = ast.literal_eval(head)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        unknown = None
    if isinstance(unknown, list) and unknown:
        return "FIELDS_UNKNOWN", {"count": len(unknown)}
    if missing:
        return "FIELDS_MISSING", {"missing": ",".join(missing)}
    return "OTHER", {}


def classify(message):
    """ContractError message text -> (rule, path, detail). Never returns model text."""
    message = str(message)
    if message.startswith("not canonical JSON: "):
        return "NOT_CANONICAL", None, {}
    if message.startswith("expected_attribute_ids: "):
        return "EXPECTED_IDS", None, {}
    if message == "inventory: sha256 differs from the pinned digest (mapping drift)":
        return "SHA_MISMATCH", None, {}
    head = _HEAD.match(message)
    if not head:
        return "OTHER", None, {}
    where, rest = head.group(1), message[head.end():]
    path = _clean_path(where)
    detail = {}
    if rest == "expected an object":
        rule = "NOT_OBJECT"
    elif rest.startswith("unknown [") and ", missing [" in rest:
        rule, detail = _fields_rule(rest)
    elif rest.startswith("expected text of 1.."):
        rule = "TEXT_LENGTH"
    elif rest == "expected a lowercase sha256 hex digest":
        rule = "SHA_FORMAT"
    elif rest == "expected four integers [x, y, w, h]":
        rule = "ROI_SHAPE"
    elif _ROI_REPR.fullmatch(rest):
        rule = "ROI_OUT_OF_FRAME"
    elif rest in ("expected photo-inventory/1", "expected photo-compare/2"):
        rule = "SCHEMA_VERSION"
    elif rest == "not the expected reference photo":
        rule = "REF_ID"
    elif rest == "expected a non-empty list" and where == "inventory.attributes":
        rule = "NO_ATTRIBUTES"
    elif rest == "duplicate":
        rule = "DUP_ATTR"
    elif rest == "not the pinned inventory":
        rule = "SHA_MISMATCH"
    elif rest == "results and unmapped_candidate_elements must be lists":
        rule = "RESULTS_NOT_LIST"
    elif rest.startswith("unknown or duplicate "):
        rule = "ATTR_UNKNOWN_OR_DUP"
    elif rest.startswith("invalid or contradictory "):
        rule = "VERDICT_EVIDENCE"
    elif rest == "must be null when not visible in reference":
        rule = "ROI_NOT_NULL"
    elif rest.startswith("missing [") and where == "response.results":
        rule = "RESULTS_MISSING"
    elif rest == "unknown value":
        rule = "VISIBILITY_VALUE"
    else:
        return "OTHER", None, {}
    return rule, path, detail


def fixed(rule, path=None, **detail):
    """Re-check a reject on its way to a log or a table: closed rule, safe path, safe detail."""
    rule = rule if rule in RULES else "OTHER"
    path = path if isinstance(path, str) and len(path) <= 80 and _SAFE_PATH.match(path) else None
    out = {}
    count = detail.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and 0 < count < 10000:
        out["count"] = count
    missing = detail.get("missing")
    if isinstance(missing, str) and _SAFE_MISSING.match(missing) and all(
            m in _FIELDS for m in missing.split(",")):
        out["missing"] = missing
    return rule, path, out


def line_fields(exc):
    """` rule=... [path=...] [count=N] [missing=a,b]` for an AnswerError, or '' when it has no rule."""
    rule = getattr(exc, "rule", None)
    if rule is None:
        return ""
    rule, path, detail = fixed(rule, getattr(exc, "path", None), **(getattr(exc, "detail", None) or {}))
    text = " rule=%s" % rule
    if path:
        text += " path=%s" % path
    if "count" in detail:
        text += " count=%d" % detail["count"]
    if "missing" in detail:
        text += " missing=%s" % detail["missing"]
    return text


class RejectTally:
    """Counts of format rejects by (stage, rule). Holds nothing else."""

    def __init__(self):
        self.counts = {}
        self.fixed = {}

    def add(self, stage, code, rule):
        stage = stage if re.fullmatch(r"[a-z_]{1,20}", str(stage)) else "unknown"
        if code == "FIXED":   # a repaired answer, counted once per call
            key = (stage, rule if rule in FIXES else "OTHER")
            self.fixed[key] = self.fixed.get(key, 0) + 1
            return
        key = (stage, rule if rule in RULES else "OTHER")
        self.counts[key] = self.counts.get(key, 0) + 1

    def lines(self):
        out = ["rejects by stage x rule:" if self.counts else "rejects by stage x rule: none"]
        for (stage, rule), n in sorted(self.counts.items()):
            out.append("  stage=%s rule=%s n=%d" % (stage, rule, n))
        if self.fixed:
            out.append("repaired answers by stage x rule:")
            for (stage, rule), n in sorted(self.fixed.items()):
                out.append("  stage=%s rule=%s n=%d" % (stage, rule, n))
        return out
