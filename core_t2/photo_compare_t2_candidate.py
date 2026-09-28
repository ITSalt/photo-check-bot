# photo_compare_t2_candidate: photo comparison core (T2), contract layer.
# T2a: ContractError, canonical JSON + sha256_hex, the shared ROI validator,
# validate_inventory, validate_response. Stdlib only, no I/O, inputs never mutated.
import copy, hashlib, json

VERDICT_ENUM = ("MATCH", "MISMATCH", "NOT_VISIBLE_IN_REFERENCE")
EVIDENCE = ("DIRECT", "PARTIAL", "NONE")
CONTRADICTORY = {("MATCH", "NONE"), ("NOT_VISIBLE_IN_REFERENCE", "DIRECT")}
VISIBILITY = ("VISIBLE_AND_ABSENT", "OCCLUDED_IN_REFERENCE")
_INV = {"schema_version", "reference_photo_id", "attributes"}
_ATTR = {"attribute_id", "claim", "reference_roi"}
_RESP = {"schema_version", "inventory_sha256", "results", "unmapped_candidate_elements"}
_RES = {"attribute_id", "verdict", "evidence_quality", "reference_roi", "candidate_roi", "observation"}
_UNM = {"candidate_roi", "description", "visibility_in_reference"}


class ContractError(ValueError):
    pass  # input breaks the contract: the whole call is rejected, never repaired


def canonical_json(obj):
    # UTF-8, sorted keys, compact separators; NaN, Infinity and non-JSON types reject.
    try:
        s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ContractError("not canonical JSON: %s" % e)
    return s.encode("utf-8")


def sha256_hex(obj):
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def _fields(obj, allowed, where):
    if not isinstance(obj, dict):
        raise ContractError(where + ": expected an object")
    if set(obj) != allowed:
        raise ContractError("%s: unknown %s, missing %s" % (where, sorted(set(obj) - allowed), sorted(allowed - set(obj))))


def _text(v, where, max_len=None):
    if not isinstance(v, str) or not v.strip() or (max_len and len(v) > max_len):
        raise ContractError("%s: expected text of 1..%s characters" % (where, max_len or "any"))
    return v


def _hex64(v, where):
    if not (isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)):
        raise ContractError(where + ": expected a lowercase sha256 hex digest")
    return v


def validate_roi(roi, where="roi"):
    # Integer permille [x, y, w, h]: x, y >= 0; w, h >= 1; x+w, y+h <= 1000; w*h >= 100.
    if not (isinstance(roi, (list, tuple)) and len(roi) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in roi)):
        raise ContractError(where + ": expected four integers [x, y, w, h]")
    x, y, w, h = roi
    if x < 0 or y < 0 or w < 1 or h < 1 or x + w > 1000 or y + h > 1000 or w * h < 100:
        raise ContractError("%s: %r is outside the frame or smaller than 100" % (where, list(roi)))
    return [x, y, w, h]


def validate_inventory(inventory, *, expected_reference_photo_id, expected_sha256=None):
    # photo-inventory/1 -> {"sha256", "reference_photo_id", "attribute_ids", "inventory"}.
    # With expected_sha256, any drift from the pinned bytes rejects (M1).
    _fields(inventory, _INV, "inventory")
    if inventory["schema_version"] != "photo-inventory/1":
        raise ContractError("inventory.schema_version: expected photo-inventory/1")
    ref = _text(inventory["reference_photo_id"], "inventory.reference_photo_id")
    if ref != expected_reference_photo_id:
        raise ContractError("inventory.reference_photo_id: not the expected reference photo")
    entries, ids = inventory["attributes"], []
    if not isinstance(entries, list) or not entries:
        raise ContractError("inventory.attributes: expected a non-empty list")
    for i, a in enumerate(entries):
        where = "inventory.attributes[%d]" % i
        _fields(a, _ATTR, where)
        if _text(a["attribute_id"], where + ".attribute_id") in ids:
            raise ContractError("%s.attribute_id: duplicate" % where)
        ids.append(a["attribute_id"])
        _text(a["claim"], where + ".claim")
        validate_roi(a["reference_roi"], where + ".reference_roi")
    digest = sha256_hex(inventory)
    if expected_sha256 is not None and digest != _hex64(expected_sha256, "expected_sha256"):
        raise ContractError("inventory: sha256 differs from the pinned digest (mapping drift)")
    return {"sha256": digest, "reference_photo_id": ref, "attribute_ids": ids, "inventory": copy.deepcopy(inventory)}


def validate_response(payload, expected_attribute_ids, *, expected_inventory_sha256):
    # Strict gate for one photo-compare/2 answer: returns a deep copy of the same shape.
    ids = expected_attribute_ids
    expected = set() if isinstance(ids, str) else set(ids)
    if not expected or not all(isinstance(a, str) and a for a in expected):
        raise ContractError("expected_attribute_ids: expected non-empty attribute ids")
    _fields(payload, _RESP, "response")
    if payload["schema_version"] != "photo-compare/2":
        raise ContractError("response.schema_version: expected photo-compare/2")
    pinned = _hex64(expected_inventory_sha256, "expected_inventory_sha256")
    if _hex64(payload["inventory_sha256"], "response.inventory_sha256") != pinned:
        raise ContractError("response.inventory_sha256: not the pinned inventory")
    results, unmapped, seen = payload["results"], payload["unmapped_candidate_elements"], set()
    if not isinstance(results, list) or not isinstance(unmapped, list):
        raise ContractError("response: results and unmapped_candidate_elements must be lists")
    for i, r in enumerate(results):
        where = "response.results[%d]" % i
        _fields(r, _RES, where)
        aid, verdict, ev = r["attribute_id"], r["verdict"], r["evidence_quality"]
        if not isinstance(aid, str) or aid not in expected or aid in seen:
            raise ContractError("%s.attribute_id: unknown or duplicate %r" % (where, aid))
        seen.add(aid)
        if verdict not in VERDICT_ENUM or ev not in EVIDENCE or (verdict, ev) in CONTRADICTORY:
            raise ContractError("%s: invalid or contradictory %r + %r" % (where, verdict, ev))
        if verdict != "NOT_VISIBLE_IN_REFERENCE":
            validate_roi(r["reference_roi"], where + ".reference_roi")
        elif r["reference_roi"] is not None:
            raise ContractError(where + ".reference_roi: must be null when not visible in reference")
        validate_roi(r["candidate_roi"], where + ".candidate_roi")
        _text(r["observation"], where + ".observation", 160)
    if seen != expected:
        raise ContractError("response.results: missing %s" % sorted(expected - seen))
    for i, u in enumerate(unmapped):
        where = "response.unmapped_candidate_elements[%d]" % i
        _fields(u, _UNM, where)
        validate_roi(u["candidate_roi"], where + ".candidate_roi")
        _text(u["description"], where + ".description", 120)
        if u["visibility_in_reference"] not in VISIBILITY:
            raise ContractError(where + ".visibility_in_reference: unknown value")
    return copy.deepcopy(payload)


IMPLEMENTED = True
# ---------------------------------------------------------------------------
# T2c: provider-adapter -- one call through a fake transport.
#
# The transport is any callable `transport(request) -> {"status": int, "body": str}`.
# `provider_call` sets no host, opens no socket, retries nothing: it invokes the
# transport exactly once and either returns the parsed response body or raises a
# typed ProviderError carrying a `.code`:
#
#   TIMEOUT        the transport raised TimeoutError (socket.timeout is the same class)
#   REFUSED        the transport answered with a status other than 200
#   MALFORMED_JSON status 200, but the body is not parseable JSON
#
# The request is passed through untouched and never mutated, and the parsed body is
# a fresh object (nothing the transport keeps is returned by reference).
PROVIDER_TIMEOUT = "TIMEOUT"
PROVIDER_REFUSED = "REFUSED"
PROVIDER_MALFORMED_JSON = "MALFORMED_JSON"
PROVIDER_ERROR_CODES = (PROVIDER_TIMEOUT, PROVIDER_REFUSED, PROVIDER_MALFORMED_JSON)
_OK_STATUS = 200


class ProviderError(Exception):
    """A transport-level failure, typed by `.code` (one of PROVIDER_ERROR_CODES)."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__("%s: %s" % (code, detail) if detail else code)


def provider_call(transport, request):
    """Run one provider call through `transport`; return the parsed body.

    Raises ProviderError with a `.code` for a timeout, a refusal or a malformed
    JSON body. Anything the transport cannot do under its own contract (it is not
    callable, it answers with something other than a {"status", "body"} mapping, the
    body is not text, or it decodes to something other than an object) fails closed as
    ProviderError too rather than passing a half-read answer on. Inputs are never mutated.
    """
    try:
        reply = transport(request)
    except TimeoutError as exc:  # a real timeout is a typed outcome, not a crash
        raise ProviderError(PROVIDER_TIMEOUT, str(exc)) from exc
    if not isinstance(reply, dict):
        raise ProviderError(PROVIDER_MALFORMED_JSON,
                            "transport answered %s, not a {status, body} mapping" % type(reply).__name__)
    status = reply.get("status")
    if not (isinstance(status, int) and not isinstance(status, bool)) or status != _OK_STATUS:
        raise ProviderError(PROVIDER_REFUSED, "transport answered status %r" % (status,))
    body = reply.get("body")
    if not isinstance(body, (str, bytes, bytearray)):
        raise ProviderError(PROVIDER_MALFORMED_JSON,
                            "body is %s, not JSON text" % type(body).__name__)
    try:
        decoded = json.loads(body)
    except (ValueError, TypeError) as exc:  # JSONDecodeError is a ValueError
        raise ProviderError(PROVIDER_MALFORMED_JSON, str(exc)) from exc
    if not isinstance(decoded, dict):
        raise ProviderError(PROVIDER_MALFORMED_JSON,
                            "body decoded to %s, not the response object" % type(decoded).__name__)
    return decoded


IMPLEMENTED = True

# ---------------------------------------------------------------------------
# T2d: verdict aggregator (contract layer, stdlib only). `aggregate_attributes` is the
# exhaustive table over the seven valid states and all 49 ordered pairs (1/1/11/32/4).
# `padded_roi` escalates +10% per side, integer half-up, clamped to 0..1000.
# `aggregate_responses` adds the coverage gate: greedy best-IoU pairing, stable index
# tie-breaks; MISMATCH only when both orders report VISIBLE_AND_ABSENT and IoU > 0.30
# (strict, integer cross multiplication); else NEEDS_REVIEW, or NO_UNMAPPED_EVIDENCE
# when neither order reports.
from fractions import Fraction

AGGREGATE_OUTCOMES = ("MATCH", "MISMATCH", "NEEDS_REVIEW", "ASYMMETRIC_PASS",
                      "NOT_VISIBLE_IN_REFERENCE")
UNMAPPED_COVERAGE = ("MISMATCH", "NEEDS_REVIEW", "NO_UNMAPPED_EVIDENCE")
_ESCALATING = ("NEEDS_REVIEW", "ASYMMETRIC_PASS")
_IOU = (3, 10)  # the gate is IoU > 0.30
_VERDICTS = ("MATCH", "MISMATCH", "NOT_VISIBLE_IN_REFERENCE")
_EVIDENCE = ("DIRECT", "PARTIAL", "NONE")


def padded_roi(roi):
    """Widen a valid [x, y, w, h] by 10% per side; integer half-up; clamp to 0..1000."""
    x, y, w, h = validate_roi(roi, "padded_roi")
    px, py = (w + 5) // 10, (h + 5) // 10  # 10% of a side, half-up
    x1, y1 = max(0, x - px), max(0, y - py)
    x2, y2 = min(1000, x + w + px), min(1000, y + h + py)
    return (x1, y1, x2 - x1, y2 - y1)


def _overlap(a, b):
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter, a[2] * a[3] + b[2] * b[3] - inter


def roi_iou_exceeds(a, b, num=_IOU[0], den=_IOU[1]):
    """True iff intersection/union > num/den (default 0.30), by integer arithmetic."""
    inter, union = _overlap(a, b)
    return union > 0 and inter * den > num * union


def _by_id(results, where):
    if not isinstance(results, list):
        raise ContractError(where + ": expected a list of results")
    out = {}
    for i, r in enumerate(results):
        if not isinstance(r, dict) or not {"attribute_id", "verdict", "evidence_quality"} <= set(r):
            raise ContractError("%s[%d]: needs attribute_id, verdict, evidence_quality" % (where, i))
        if r["verdict"] not in _VERDICTS or r["evidence_quality"] not in _EVIDENCE:
            raise ContractError("%s[%d]: verdict or evidence out of vocabulary" % (where, i))
        if r["attribute_id"] in out:
            raise ContractError("%s: duplicate attribute_id %r" % (where, r["attribute_id"]))
        out[r["attribute_id"]] = r
    return out


def aggregate_attributes(left_results, right_results):
    """The per-attribute verdict table for the two comparison orders (all 49 pairs)."""
    left = _by_id(left_results, "aggregate_attributes.left")
    right = _by_id(right_results, "aggregate_attributes.right")
    if set(left) != set(right):
        raise ContractError("aggregate_attributes: attribute ids differ between orders")
    table = {}
    for aid, l in left.items():
        r = right[aid]
        if l["verdict"] != r["verdict"]:
            table[aid] = "ASYMMETRIC_PASS"
        elif l["verdict"] == "NOT_VISIBLE_IN_REFERENCE":
            table[aid] = "NOT_VISIBLE_IN_REFERENCE"
        elif l["evidence_quality"] == r["evidence_quality"] == "DIRECT":
            table[aid] = l["verdict"]  # MATCH or MISMATCH
        else:
            table[aid] = "NEEDS_REVIEW"
    return table


def _coverage(left, right):
    if not isinstance(left, list) or not isinstance(right, list):
        raise ContractError("aggregate_responses: unmapped_candidate_elements must be lists")
    for side, items in (("left", left), ("right", right)):
        for k, u in enumerate(items):
            roi = u.get("candidate_roi") if isinstance(u, dict) else None
            if not (isinstance(u, dict) and "visibility_in_reference" in u
                    and isinstance(roi, (list, tuple)) and len(roi) == 4
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in roi)
                    and roi[2] >= 1 and roi[3] >= 1):
                raise ContractError(
                    "aggregate_responses.%s.unmapped[%d]: need candidate_roi [x,y,w,h] and visibility" % (side, k))
    if not left and not right:
        return "NO_UNMAPPED_EVIDENCE"
    cands = []
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            inter, union = _overlap(a["candidate_roi"], b["candidate_roi"])
            if inter > 0:
                cands.append((Fraction(inter, union), i, j))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_l, used_r, review = set(), set(), False
    for _, i, j in cands:
        if i in used_l or j in used_r:
            continue
        used_l.add(i)
        used_r.add(j)
        a, b = left[i], right[j]
        if not (a["visibility_in_reference"] == b["visibility_in_reference"] == "VISIBLE_AND_ABSENT"
                and roi_iou_exceeds(a["candidate_roi"], b["candidate_roi"])):
            review = True
    if len(used_l) != len(left) or len(used_r) != len(right):
        review = True
    return "NEEDS_REVIEW" if review else "MISMATCH"


def aggregate_responses(left, right):
    """Product verdict {"attributes", "coverage", "escalations"} (escalation = padded ROI)."""
    for name, side in (("left", left), ("right", right)):
        if not isinstance(side, dict) or "results" not in side or "unmapped_candidate_elements" not in side:
            raise ContractError("aggregate_responses.%s: need results and unmapped_candidate_elements" % name)
    attributes = aggregate_attributes(left["results"], right["results"])
    coverage = _coverage(left["unmapped_candidate_elements"], right["unmapped_candidate_elements"])
    boxes = {}
    for side in (left, right):
        for r in side["results"]:
            if r.get("reference_roi") is not None and r["attribute_id"] not in boxes:
                boxes[r["attribute_id"]] = r["reference_roi"]
    escalations = {aid: (padded_roi(boxes[aid]) if aid in boxes else None)
                   for aid, o in attributes.items() if o in _ESCALATING}
    return {"attributes": attributes, "coverage": coverage, "escalations": escalations}


IMPLEMENTED = True

# ---------------------------------------------------------------------------
# T2e: eval-runner -- a blinded manifest in, raw outcomes and per-family counts out.
# Stdlib only, pure (rows never mutated, fresh copies out), fail-closed.
#
# A row is the disclosure record -- case_id, family, kind, outcome, guard_outcome,
# provider_called, counterfactual_falsifier, planter, grader, as_of -- and exactly
# that: a photo, an inventory digest or a model answer is not a row, and an unknown
# field rejects rather than being copied.
EVAL_KINDS = ("PLANTED_DEFECT", "BENIGN_NUISANCE", "UNINVENTORIED_ADDITION", "M1_MAPPING_DRIFT")
M1_MAPPING_DRIFT = "M1_MAPPING_DRIFT"
REJECTED_PRE_PROVIDER = "REJECTED_PRE_PROVIDER"
M1_COUNTERFACTUAL_FALSIFIER = "SAME_AS_BASELINE_IS_FAILURE"
# The outcomes a row may report. Written out rather than read off
# `AGGREGATE_OUTCOMES`/`UNMAPPED_COVERAGE`: T2e's declared dependency is T2a alone,
# so this section has to stand when it is merged without T2d. The extra suite asserts
# the tuple still equals those two constants, so a divergence fails loudly.
EVAL_OUTCOMES = ("MATCH", "MISMATCH", "NEEDS_REVIEW", "ASYMMETRIC_PASS",
                 "NOT_VISIBLE_IN_REFERENCE", "NO_UNMAPPED_EVIDENCE", "REJECTED_PRE_PROVIDER")
_EVAL_ROW = {"case_id", "family", "kind", "outcome", "guard_outcome", "provider_called",
             "counterfactual_falsifier", "planter", "grader", "as_of"}


def _eval_row(row, where):
    """Validate one manifest row; return (case_id, family, outcome)."""
    _fields(row, _EVAL_ROW, where)
    case_id = _text(row["case_id"], where + ".case_id")
    family = _text(row["family"], where + ".family")
    if row["kind"] not in EVAL_KINDS:
        raise ContractError("%s.kind: %r is not one of the preregistered case kinds" % (where, row["kind"]))
    outcome = row["outcome"]
    if outcome not in EVAL_OUTCOMES:
        raise ContractError("%s.outcome: %r is not one of the eval outcomes" % (where, outcome))
    guard = _text(row["guard_outcome"], where + ".guard_outcome")
    if not isinstance(row["provider_called"], bool):
        raise ContractError(where + ".provider_called: expected true or false")
    falsifier = _text(row["counterfactual_falsifier"], where + ".counterfactual_falsifier")
    planter = _text(row["planter"], where + ".planter")
    grader = _text(row["grader"], where + ".grader")
    if planter.strip() == grader.strip():
        raise ContractError("%s: planter and grader must be different people or sessions" % where)
    as_of = row["as_of"]
    if not (isinstance(as_of, int) and not isinstance(as_of, bool)) or as_of < 0:
        raise ContractError(where + ".as_of: expected a non-negative integer timestamp")
    if row["kind"] == M1_MAPPING_DRIFT or outcome == REJECTED_PRE_PROVIDER:
        if outcome != REJECTED_PRE_PROVIDER or guard != REJECTED_PRE_PROVIDER:
            raise ContractError("%s: a rejection is pre-provider, got outcome %r with guard %r"
                                % (where, outcome, guard))
        if row["provider_called"]:
            raise ContractError("%s: a rejected row is never sent to a provider" % where)
        if falsifier != M1_COUNTERFACTUAL_FALSIFIER:
            raise ContractError("%s.counterfactual_falsifier: M1 keeps %s"
                                % (where, M1_COUNTERFACTUAL_FALSIFIER))
    return case_id, family, outcome


def summarize_eval(rows):
    """Summarise a blinded eval manifest: {"raw_outcomes", "counts", "total"}.

    `counts` is per family, then per outcome. `raw_outcomes` is every input row, in
    order, as a fresh copy. Pure: `rows` is never mutated.
    """
    if not isinstance(rows, list):
        raise ContractError("summarize_eval: expected a list of rows")
    raw, counts, seen = [], {}, set()
    for i, row in enumerate(rows):
        case_id, family, outcome = _eval_row(row, "eval.rows[%d]" % i)
        if case_id in seen:
            raise ContractError("eval.rows[%d].case_id: duplicate %r" % (i, case_id))
        seen.add(case_id)
        by_outcome = counts.setdefault(family, {})
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        raw.append(copy.deepcopy(row))
    return {"raw_outcomes": raw, "counts": {f: counts[f] for f in sorted(counts)}, "total": len(raw)}


IMPLEMENTED = True

# ---------------------------------------------------------------------------
# T2b: prompt-builder -- build two comparison calls with swapped image positions
# but fixed semantic roles. Fails closed on missing/invalid digest, invalid inventory
# (via validate_inventory), or matching photo IDs.


def build_ordered_calls(
    inventory, inventory_sha256, reference_photo_id, candidate_photo_id
):
  """Build the two comparison calls with swapped image positions but fixed roles.

  Both calls carry identical validated inventory bytes and digest.
  Fails closed on:
    1. inventory_sha256 missing, None, or not 64-hex lowercase.
    2. invalid inventory or mapping drift (via validate_inventory).
    3. reference_photo_id == candidate_photo_id, or non-text photo IDs.
  """
  if inventory_sha256 is None:
    raise ContractError("inventory_sha256: missing or None")
  digest = _hex64(inventory_sha256, "inventory_sha256")
  ref = _text(reference_photo_id, "reference_photo_id")
  cand = _text(candidate_photo_id, "candidate_photo_id")
  if ref == cand:
    raise ContractError(
        "reference_photo_id and candidate_photo_id must be distinct"
    )

  validated = validate_inventory(
      inventory, expected_reference_photo_id=ref, expected_sha256=digest
  )
  clean_inv = validated["inventory"]

  first = {
      "images": [
          {"photo_id": ref, "role": "reference"},
          {"photo_id": cand, "role": "candidate"},
      ],
      "inventory": copy.deepcopy(clean_inv),
      "inventory_sha256": digest,
  }
  second = {
      "images": [
          {"photo_id": cand, "role": "candidate"},
          {"photo_id": ref, "role": "reference"},
      ],
      "inventory": copy.deepcopy(clean_inv),
      "inventory_sha256": digest,
  }
  return first, second
