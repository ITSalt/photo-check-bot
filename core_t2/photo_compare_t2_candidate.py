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
