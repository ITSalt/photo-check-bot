# T3 photo checker integration — public contract v1.1

Task ID: `photo-checker-t3-contract-v1`

This bundle defines observable boundaries for the next cut after the merged T2
comparison core. It does not select or call a real vision provider and contains
no seller photos.

## Candidate module

Implement `photo_compare_t3_candidate.py` with:

- `run_bot_pair(session, compare, logger) -> dict`
- `call_provider(request, transport, environ, logger) -> dict`
- `summarize_blind_eval(rows) -> dict`
- exception class `ProviderUnavailable`

## T3a — bot wiring on a fake comparison callable

`session.photos()` returns `(reference_bytes, candidate_bytes)`. Pass those exact
two objects to `compare`, in that order, exactly once. Always call
`session.delete_all()` before returning, whether the comparison succeeds, times
out, raises an expected or unexpected exception, or returns malformed data.

The result is exactly:

```json
{"schema_version":"photo-bot-result/1","status":"MATCH|MISMATCH|NEEDS_REVIEW|UNAVAILABLE"}
```

Timeout, provider failure, any other comparison exception, an unknown status,
or a malformed result becomes `UNAVAILABLE`, never a guessed verdict. If cleanup
fails, do not return a result: propagate the cleanup error. Logs and returned
data must not contain photo bytes, photo hashes, provider secrets, or raw
exception text.

The tests establish cleanup through the injected session and inspect observable
logs/returns. They do not prove absence of hidden filesystem or network side
effects; recipient code review and sandboxing remain required.

## T3b — provider boundary

The provider key is read only from `environ["VISION_API_KEY"]`. Missing/empty
keys fail before transport. Call the injected transport exactly once with an
Authorization header and finite timeout. Timeout, non-200, or malformed body
raises `ProviderUnavailable` with a typed public code; never log or return the
key, Authorization header, raw response, or raw exception text.

The provider host and model are intentionally not selected here. The transport
is fake; network routing, provider privacy terms, price and quality are not
tested.

## T3c — blind evaluation ledger

Accept exactly 40 rows: four declared `family` values, ten rows per family,
split 5 `CLEAN` / 5 `DEFECT`. Every row has a unique `case_id`, distinct
case-folded `planter_id` and `grader_id`, `source_kind`, nuisance label
`NONE|LIGHT|CROP|ROTATION`, and one T2 outcome.

Publish exactly three primary numbers:

1. `planted_defect_false_accepts`: `DEFECT` rows returned as `MATCH`;
2. `clean_false_rejects`: `CLEAN` rows returned as `MISMATCH`;
3. `unresolved`: every outcome other than `MATCH` or `MISMATCH`.

Also preserve all raw rows and a nuisance/ground-truth/outcome breakdown. Do not
collapse these counts into an accuracy percentage. Synthetic rows must remain
labelled `SYNTHETIC`; this contract is not evidence about real seller photos.

## Acceptance and dependency cut

The public suite must pass 8/8. Implementation order is T3a first; T3b remains
behind the same interface; T3c compares candidate providers after the owner
chooses allowed transports. A failing implementation remains its own task result
and does not split this contract task.
