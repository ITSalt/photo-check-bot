# T4: vision transport and request glue

Modules in `app/`:

- `vision_transport.py`: `make_transport(base_url, timeout_cap, host_allowlist=...)`.
  HTTPS only, host allowlist, no redirects (3xx is returned as a status, not followed),
  no environment proxies, 1 MiB response cap. `TimeoutError` passes through for T3b.
- `vision_request.py`: image hygiene (JPEG/PNG/WebP by magic bytes, 8 MiB cap each,
  longest side at most 1536 px read from the header, nothing re-encoded), the inventory
  request, and extraction of the model's JSON answer.
  `chat_payload` builds every request body (inventory and both compares) with
  `temperature: 0`, `max_tokens: 2800`, JSON output, minimal reasoning, `"provider": {"data_collection": "deny"}`
  (D-2: only providers that neither store nor train on the request may serve it) and
  `"usage": {"include": true}` (the reply carries the cost for the spend cap).
- `vision_core.py`: the chain through the T2 core (`core_t2/`).
- `vision_compare.py`: `compare_from_env(environ)` returns the `compare` callable for
  `run_bot_pair`, or `None` when unconfigured (the bot then behaves as before).

## The check (3 provider calls)

1. **Inventory call**: the reference photo alone; the model lists attributes
   (`attribute_id`, `claim`, permille `reference_roi`). This follows the T2 design
   (praktik, "reference-only inventory", thread `86b24834`). We wrap it as
   `photo-inventory/1` with `reference_photo_id="reference"` and run T2
   `validate_inventory`; its SHA-256 is pinned. At most 12 attributes.
2. **Two compare calls**: T2 `build_ordered_calls` gives the two image orders (roles
   stay attached to the photos, only positions swap, identical inventory bytes). The
   model answers `photo-compare/2`, echoing the inventory digest, plus one field of ours per
   result, `candidate_view` (see "Out-of-view details" below), which `parse_compare` takes out
   first. Each answer then goes through T2 `validate_response`; a bad answer stops the chain
   (no third call).
3. T2 `aggregate_responses` combines both orders. Each of the three calls goes through
   the real T3b adapter, `core_t3/photo_compare_t3_candidate.py` `call_provider(request,
   transport, environ, logger)`; there is no other provider path and it is not
   injectable (`compare_from_env` resolves it itself). A `ProviderUnavailable` from it
   (`MISSING_KEY`, `TIMEOUT`, `HTTP_ERROR`, `MALFORMED_BODY`) at any stage ends the check
   with `UNAVAILABLE`; no further call is made.

## Status rule (T4.2)

T2 decides every per-attribute outcome and the unmapped-element coverage. On top of that,
`vision_core.assess` returns the check result:

```
{"status", "total", "confirmed": [attribute_id],
 "to_check": [{"attribute_id", "label", "reason"}],      # reason: not_visible | orders_disagree |
 "differs": [{"attribute_id", "label"}],                 #         weak_evidence | fine_detail_unverified
 "extra": "none" | "unconfirmed" | "confirmed",
 "not_compared": [{"attribute_id", "label"}],            # out of view in the candidate (D-10)
 "reason": "too_few_compared"}                           # only when the MATCH minimum alone failed
```

The status is rule A' (`vision_core.rule_a_prime`; rule A until WP-CONTROLS-04, see below):

- `MISMATCH`: some attribute is a both-order DIRECT mismatch (present in the reference,
  clearly absent or different where it should be visible), or an extra product element is
  confirmed in both orders. Unchanged from rule A.
- `MATCH`: nothing differs, nothing is left to check, neither order reported an extra, AND at
  least 3 attributes are confirmed (both-order DIRECT match) AND confirmed + to_check is at
  least half of the inventory, rounded up. Out-of-view attributes are neither confirmed nor
  to check.
- `NEEDS_REVIEW`: anything else, with the `to_check` list saying what and why; `reason` is
  `too_few_compared` when only the minimum failed.
- `UNAVAILABLE`: provider failure or an unusable answer.

### Out-of-view details are not comparable (WP-CONTROLS-04, D-10)

Trigger: the owner's live run of 11.10, a baby bodysuit with the logo on the inner side, shown
worn on the generated photo; the logo was listed under "Проверьте сами" as "не видно".

- **Field.** The compare prompt asks, for every result, `candidate_view`: `IN_VIEW` (the place
  of this detail on the product is inside the candidate's frame and faces the camera) or
  `OUT_OF_VIEW` (that place cannot be seen in the candidate: inner side, lining, back or sole
  when another side is shown, covered by wear or by another part, cropped out). The prompt
  adds: "If that place is inside the frame and faces the camera but the detail is missing or
  different there, it is IN_VIEW (answer MISMATCH), never OUT_OF_VIEW." The T2
  contract (`core_t2`) is unchanged and rejects extra fields, so `vision_core.parse_compare`
  pops the field out of every result BEFORE `validate_response` and keeps it beside the
  validated answer as `"candidate_view": {attribute_id: "IN_VIEW" | "OUT_OF_VIEW"}`. Only the
  two enum strings are kept. A missing or invalid value becomes `IN_VIEW` (the conservative
  default: nothing is hidden from the seller) and is counted as the fix code `VIEW_DEFAULTED`;
  the value itself is dropped in the parser and never reaches a log, the code record or the
  notes. The field anywhere else (top level, an unmapped element) is still a format reject.
- **Class.** `out_of_view` needs ALL of: both orders say `OUT_OF_VIEW`, both orders give
  evidence `NONE`, neither verdict is MATCH. One order only, or any PARTIAL / DIRECT evidence,
  keeps the class the attribute had before (`not_visible`, `weak_evidence`, `differs`, ...):
  a detail the model saw differ stays a MISMATCH whatever view it claims.
- **Rule A'.** An `out_of_view` attribute is listed in `not_compared`, not in `to_check`, and
  by itself never turns MATCH into anything else. The MATCH minimum (3 confirmed, half of the
  inventory compared) is the safeguard against "almost everything was out of view".
- **What changed against rule A when nothing is out of view:** only the minimum. An inventory
  of 1 or 2 attributes, all confirmed, was MATCH and is now NEEDS_REVIEW with
  `too_few_compared` (`tests/test_out_of_view.py` `PropertyTests` checks that this is the only
  difference over random answers).
- **Reply.** `app/verdict_notes.py`: out-of-view items never appear under "Проверьте сами" /
  "Check yourself". They get one neutral line, the only note a MATCH can carry:
  "Не сравнивалось (на ИИ-фото это место не видно): <labels>." /
  "Not compared (this place is not visible on the AI photo): <labels>." `too_few_compared`
  adds "Сравнить удалось слишком мало деталей, поэтому посмотрите фото сами." /
  "Too few details could be compared, so please look at the photos yourself."
- **Prompt size.** The compare instruction grew from 2,238 to 2,726 bytes; the 12-attribute
  inventory of `tests/test_call_ceiling.py` gives a 5,211-byte prompt (4,723 before) against
  the 6,000-byte bound, a margin of 789 bytes.
- **Limit.** The view is the model's own claim, like every other field: a different product
  shown from another side could have its differing details called out of view. The minimum
  bounds how much can be left out, and the controls (D-10 safeguard 4: zero false MATCH,
  including different-product pairs in different views) are the check.

The T3a result of `run_bot_pair` stays exactly `{"schema_version", "status"}` (the public
contract says "exactly"). The lists ride beside it: `compare` returns them,
`PhotoChecks` keeps them for the reply and `app/verdict_notes.py` formats them, e.g.
"Совпадает: 9 из 12. Проверьте сами: застёжка (не видно), текст логотипа (мелкая деталь)."
Labels come from the model's `claim` ("name: state"; the name before the colon, letters,
digits and `-,()%` only, 40 characters). They are model text shown to the seller, never logged.

### How each review point maps onto the T2 schema

| Point | In the schema? | What was done |
|---|---|---|
| 1 product attributes only | no field for it | prompt-level exclusion list (background, scene, lighting, angle, props, hands, people) |
| 2 confirmed / to_check | derived from outcomes | reasons computed in `assess`; no new field sent to or from the model |
| 3 not visible, two kinds | **gap**: T2 has `NOT_VISIBLE_IN_REFERENCE` only; nothing for "hidden in the candidate" | convention on existing fields: hidden in the candidate = `MISMATCH` with `evidence_quality` NONE (never DIRECT), which T2 aggregates to `NEEDS_REVIEW`; shown as `not_visible`. Present in the reference and clearly absent in the candidate = `MISMATCH` + DIRECT in both orders = `MISMATCH` (T2 distinguishes this through evidence quality). Status for hidden stays `NEEDS_REVIEW` |
| 4 extras: product yes, scene no | **gap**: unmapped elements have no kind field, and the schema rejects extra fields | prompt-only ("ONLY parts of the product itself"). Code cannot filter: a model that lists the table in both orders gets `MISMATCH` (pinned by `test_known_gap_scene_extra_is_prompt_only`) |
| 5 fine-detail | **gap**: the inventory has no marker | fine detail = reference box under 2% of the frame (`FINE_ROI_AREA = 20000` permille squared; the prompt asks for tight boxes). A fine attribute that T2 escalates (weak evidence or order disagreement) is labelled `fine_detail_unverified`. Crop escalation itself is not run (it would need image decoding or re-encoding, which this cut avoids) |

Open question for the T2 authors / strategist on point 5: the request was that crop
escalation blocks MATCH only for fine details. As built, T2's own gate stays: any attribute
that is not a both-order DIRECT match blocks MATCH, coarse or fine; "fine" only changes the
label. Letting a coarse PARTIAL+PARTIAL match count as confirmed would override the T2
table (`MATCH` needs DIRECT in both orders) and is not done here.

## Limit: both orders are one witness asked twice

Swapping the image order cancels position bias. It does not make the two answers
independent: it is the same model, with the same blind spots, looking at the same pixels.
Two agreeing answers mean "this model, asked twice, says the same", not "two reviewers
agree". A product difference the model cannot see, or hallucinates away consistently, passes
both orders. MATCH therefore means "the checked details matched in the model's reading",
which is what the bot text says (it is not a guarantee).

## How often an honest pair ends in review

N attributes; p = chance that one attribute of an honest pair is a both-order DIRECT match;
q = chance of a spurious extra-element report. Treating attributes as independent, the
share of honest pairs that is MATCH is `p^N (1-q)`, and the rest goes to review (or, rarely,
to a false MISMATCH): `review = 1 - p^N (1-q)` (`vision_core.expected_review_share`).

| N | p | MATCH | review |
|---|---|---|---|
| 6 | 0.98 | 88.6% | 11.4% |
| 6 | 0.95 | 73.5% | 26.5% |
| 8 | 0.98 | 85.1% | 14.9% |
| 12 | 0.99 | 88.6% | 11.4% |
| 12 | 0.98 | 78.5% | 21.5% |
| 12 | 0.95 | 54.0% | 46.0% |
| 12 | 0.98, q=0.10 | 70.6% | 29.4% |

Every extra attribute costs: at p=0.95, going from 6 to 12 attributes doubles the review
share. p is unmeasured; the controls below measure the product `p^N` directly. The
independence assumption is only a guide: failures within one pair are correlated (a blurry
photo fails many attributes at once), which makes the true review share lower than the table
for the same average p, and the two orders are not independent either (limit above).

## Controls for the smoke test

Manifest (JSON list): `{"kind": "same"|"recrop"|"different", "ref": path, "cand": path}`.
- positive `same`: a photo against itself (`cand` may be omitted);
- positive `recrop`: the photo against a slightly cropped, re-encoded copy, e.g.
  `convert in.jpg -crop 96%x96%+12+12 +repage -quality 80 recrop.jpg` (ImageMagick);
- negative `different`: two different products of one category.

```
VISION_...=... python3 -m app.vision_controls manifest.json --repeats 3
```

Prints per kind and pooled positives the share of MATCH / MISMATCH / NEEDS_REVIEW /
UNAVAILABLE (UNAVAILABLE counts against the rule). Expected: positives near 100% MATCH,
negatives MISMATCH. The NEEDS_REVIEW share on both is the number to read. Rule: positive
MATCH below 90% prints `RULE TOO STRICT` and exits 1. At least 10 distinct photos per kind
is advisable (each run costs three calls; the manifest is the only input, no paths are
printed). Fixed seed is not needed: temperature is 0, repeats show provider jitter.

### Pre-demo mode and `--gate`

Default mode (10-20 photos, the pre-demo run) behaves as above and adds one line,
`false MATCH on negatives: <k>`; it exits 1 when k > 0 (a `different` pair judged MATCH).

```
VISION_...=... python3 -m app.vision_controls manifest.json --repeats 3 --gate
```

`--gate` is the completion check. Before any provider call the manifest is validated
(`app/controls_gate.py`): at least 20 positive rows (`same` + `recrop`) and at least 20
`different` rows, every `different` row carries an `https://` `licence`, no duplicates, no unknown
keys; otherwise it prints the row index and a fixed code and exits 2 with zero calls. After the
runs (repeats count as runs) it passes only if positive MATCH >= 90% and negative MISMATCH >= 90%;
UNAVAILABLE counts against both sides. It prints one line per side and `CONTROLS PASS` (exit 0)
or `CONTROLS FAIL: <sides>` (exit 1); sides are `positive`, `negative` and `false_match` (any MATCH on `different` rows fails the gate, even above 90%). A `recrop` row whose `cand` equals `ref` is refused (exit 2)
in both modes. The output never contains paths, URLs or model text.

### Zoom check for fine details: an experiment (`--zoom`, WP-CONTROLS-05, D-12 a)

Measured, not shipped. The bot does not run it and the production rule A' is unchanged; the
code is `app/vision_controls_zoom.py`, used only by the controls tool. It answers one question
for the spend decision of D-12: what a second, closer look at small details would change and
what it would cost.

```
VISION_...=... python3 -m app.vision_controls manifest.json --repeats 3 --zoom ask  --codes ask.codes
VISION_...=... python3 -m app.vision_controls manifest.json --repeats 3 --zoom crop --codes crop.codes
VISION_...=... python3 -m app.vision_controls manifest.json --repeats 3 --zoom both --codes zoom.codes
```

After every completed check the tool makes extra provider calls for the attributes that are
**fine** (reference box under 2% of the frame, the fine bit of the code record) **and
confirmed**:

- `ask`: one extra call per check. The same two photos and a short prompt listing only those
  attributes with their boxes (the reference box of the inventory, and the candidate box: the
  union of the two orders' boxes).
- `crop`: one extra call per attribute, smallest reference box first, at most 4 per check (the
  attributes beyond the cap are not asked and stay as they were). Each call carries two images:
  the reference cut at its box and the candidate cut at its box, both padded by half the box's
  longer side on every side, made square (at least 64 px) and scaled so that the longer side is
  768 px. Needs Pillow on the machine that runs the controls (`pip install Pillow`); it is not in
  the image, the Dockerfile or CI. Before any provider call the tool cuts one test crop: without
  Pillow, or with one that cannot cut, it prints one line and exits 2 with zero calls. EXIF
  rotation is not applied (the boxes are the model's, over the picture as sent).
- `both`: both modes after the same check, so the two are compared on the same three base
  answers and the base check is paid once.

Per attribute the model answers `PRESENT_SAME`, `ABSENT_OR_DIFFERENT` or `CANNOT_TELL` (strict
JSON, small `max_tokens`). The rule that is measured: `ABSENT_OR_DIFFERENT` turns the attribute
into "differs" (so the check would be MISMATCH), `CANNOT_TELL` into "to check" (NEEDS_REVIEW
unless something differs), `PRESENT_SAME` leaves it confirmed; then rule A' is applied again,
extra-element outcome kept. A zoom answer can only move a fine confirmed attribute to "to check"
or "differs", never anything the other way, so the status with zoom is never better than without.
A bad zoom answer (provider failure, cut-off, not JSON, a missing, repeated or unknown value)
leaves the attribute as it was and is counted. A check that is already MISMATCH is not zoomed
(nothing could change), nor is a check with no fine confirmed attribute; an UNAVAILABLE run has
nothing to zoom. `--zoom` cannot be combined with `--gate`.

The usual lines of the run (shares per kind, `false MATCH on negatives`, the exit code, the
`check_codes` lines) are the production ones, untouched. After them, per mode:

| Line | How to read it |
|---|---|
| `<kind> runs=N MATCH=a->b MISMATCH=a->b NEEDS_REVIEW=a->b UNAVAILABLE=a->b` | runs of that kind (and pooled `positive`) by status: `a` without zoom, `b` with it |
| `false MATCH on negatives: a -> b` | `different` runs judged MATCH without and with zoom: the blind spot the zoom is meant to close |
| `positive MATCH share: x% -> y%` | the price on honest pairs: the share the zoom turns into NEEDS_REVIEW or MISMATCH is `x - y` |
| `row=<i> kind=<kind> runs=<n> MATCH=a->b` | the same per manifest row (0-based index, repeats pooled): false MATCH per pair |
| `answers: PRESENT_SAME=.. ABSENT_OR_DIFFERENT=.. CANNOT_TELL=.. bad=.. over_cap=..` | asked attributes by answer; `bad` kept their class; `over_cap` were not asked (`crop` only) |
| `zoom errors by code:` | fixed codes: provider (`TIMEOUT`, `HTTP_ERROR`, ...), answer (`CUT_OFF`, `NOT_JSON`, ...), `BAD_ITEM` (one attribute without a valid answer), not sent (`PROMPT_TOO_LONG`, `CROP_FAILED`, `CROP_SIZE`), `ZOOM_FAILED` |
| `extra calls per check, <group> (n=..): min p50 p95 max` | zoom calls on top of the three of the check; nearest-rank percentiles |
| `extra cost per check USD, <group> (n=..): p50 p95 max` | the sum of the provider's `usage.cost` of those calls |
| `zoom calls=.. total cost USD=.. calls without a reported cost=..` | calls without a cost are not in the cost numbers: with any, the cost lines are a lower bound |
| `worst case of one zoom call: .. USD (within the per-call hold 0.02 USD); at most N call(s) per check = .. USD` | from the constants, see below |

The two groups are `all completed checks` and `checks that are MATCH without zoom`. The second is
what a production rule that zooms only before saying MATCH would pay.

With `--codes FILE` every completed run also gets one line per mode after its `check_codes` line
(`app.recompute` skips it):

```
kind=different zoom_codes v=1 row=3 mode=ask a=MATCH z=MISMATCH calls=1 codes=3S,5A
```

`a` is the production status, `z` the status with zoom, `codes` the asked attributes as
`<inventory position><letter>` (`-` when none): `S` PRESENT_SAME, `A` ABSENT_OR_DIFFERENT, `T`
CANNOT_TELL, `E` bad answer, `K` over the cap. The log gets `zoom_call mode=.. usage_prompt=..
usage_completion=.. cost=..` per call. Nothing else is written: no claim, box, attribute id, path
or model text.

Worst case of one zoom call (same pricing basis as the per-call ceiling below; `zoom_payload`
enforces the text bound and `max_tokens`, `build_crop_request` the crop size):

| Mode | Images | Text bound | `max_tokens` | Worst case of one call | Calls per check | Worst case per check |
|---|---|---|---|---|---|---|
| `ask` | 2 x 3096 tokens (the photos) | 4000 bytes | 600 | (6192 + 4000 + 300) x 0.75 / 1e6 + 600 x 3.75 / 1e6 = 0.010119 USD | 1 | 0.010119 USD |
| `crop` | 2 x 1548 tokens (768 px crops: at most 6 tiles of 258) | 1500 bytes | 200 | (3096 + 1500 + 300) x 0.75 / 1e6 + 200 x 3.75 / 1e6 = 0.004422 USD | at most 4 | 0.017688 USD |

Both are within the 0.02 USD hold of one call, but they are calls on top of the three that the
0.06 USD hold of a check covers: shipping either mode needs a larger hold per check (one more
call for `ask`, up to four for `crop`, or one more hold sized for them). That is the decision
these numbers are for; the controls run itself has no spend cap.

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `VISION_BASE_URL` | yes | full chat-completions URL, https, e.g. `https://openrouter.ai/api/v1/chat/completions` |
| `VISION_MODEL` | yes | e.g. `google/gemini-3.6-flash` (`[A-Za-z0-9._:/-]`, up to 100) |
| `VISION_HOST_ALLOWLIST` | yes | comma-separated hosts; the URL host must be listed, e.g. `openrouter.ai` |
| `VISION_API_KEY` | yes | read only by `call_provider` (T3b); never logged |
| `CHECK_COUNTER_SALT` | yes, for the bot | key of the free-check counter (D-12), at least 32 characters; unset or short keeps the vision check off |
| `VISION_TIMEOUT` | no | seconds, `0 < t <= 120`, default 30; the transport caps each call at it, and T3b asks for 30 s per call, so values above 30 have no effect |
| `PAIR_REMINDER_MIN` | no | second photo reminder after 5 min (`PAIR_REMINDER_MIN`, 1..25); default 5 |

If a required variable is unset or empty, `compare_from_env` returns `None` and logs
only the names of the unset variables. If values are set but invalid it raises
`VisionConfigError` (no values in the message). Nothing is hardcoded as a default:
provider and model are the owner's choice.

Wiring (WP-BOT-05): `python -m app.bot` calls `app.vision_wiring.build_photo_checks`,
which returns `PhotoChecks` only when all of these hold, else `None` and the bot replies
exactly as before:

- `compare_from_env(os.environ)` is not `None` (an invalid config logs and stays off);
- `CHECK_COUNTER_SALT` is set and at least 32 characters (else a names-only warning);
- the free-check counter file (`DATA_DIR/check_counter.json`) is readable;
- one `SpendCap(DATA_DIR/spend_cap.json, VISION_DAILY_CAP_USD, 0.02, 3)` is created at
  startup; `CapConfigError` logs `spend_cap_error code=CONFIG`, a second process
  `spend_cap_error code=SECOND_WRITER`.

Per check: the free-check counter allows (3 per user, D-12) -> `reserve()` -> download
(`app/session_factory.py`, a fresh 0o700 directory under the temp dir) -> compare, with
`begin()` before each provider call (False: nothing is sent, the check ends UNAVAILABLE,
not charged, `spend_cap_error code=BEGIN`) and `record(usage.cost)` after it (a failed call
or a reply without a cost counts the 0.02 fallback; `not_sent()` instead only when the
transport was never invoked, e.g. `MISSING_KEY`) -> the counter is charged only on MATCH or MISMATCH (D-11) ->
the reservation is closed. A counter or cap refusal downloads nothing and calls nothing.
If a check's recorded cost passes its hold (3 x 0.02 USD) or today's ledger total passes the
cap, its remaining calls are skipped and it ends UNAVAILABLE, not charged (`spend_cap_overrun`).

The free-check counter stores rows `{"key", "used", "last"}` only: `key` is
HMAC-SHA256(`CHECK_COUNTER_SALT`, user id), `last` the UTC date of the last counted
check; rows older than 90 days are purged at startup and once a day. The
real T3b `call_provider` is on main (#38), so a configured bot makes real calls (earlier
versions of this note described a `NotImplementedError` stub; that is gone). The
Dockerfile ships `core_t2/photo_compare_t2_candidate.py` and
`core_t3/photo_compare_t3_candidate.py` next to `app/` (not the public contract test
folders); CI runs `python -c "import app.vision_compare, core_t2, core_t3"` in the built
image and `tests/test_image_layout.py` checks the same layout from the Dockerfile. If
either core is not importable, the feature stays off.

## Uncharged-check limit (WP-BOT-10, D-15)

NEEDS_REVIEW, UNAVAILABLE and overrun checks are free and not stored, so each user is capped
at `UNCHARGED_DAILY_LIMIT` of them per UTC day (default 10; empty or unset -> 10; invalid ->
10 with `uncharged_limit_config code=INVALID`). The counts live in memory only, keyed by the
same HMAC key, reset at UTC midnight and at restart (accepted: restarts are rare); nothing is
written to disk, so `check_counter.json` keeps `{key, used, last}`. After the counter allows
and before `reserve()`, a user at the limit is refused for ANY check until UTC midnight (the
outcome is unknown before the call): reply `uncharged_limit` (ru/en), log
`check_refused code=UNCHARGED_LIMIT`, no download, no provider call, no reservation.
MATCH and MISMATCH do not count. `/forget` clears the user's in-memory count too.

## Logging

Per provider call (three per check), from the provider `usage` field, which every
request asks for with `"usage": {"include": true}` (`n/a` when a reply lacks it): `vision_call model=... usage_prompt=... usage_completion=... cost=...
stage=inventory|compare_a|compare_b`. Failures log stage and a public code only
(`vision_unavailable stage=compare_a code=TIMEOUT`, `vision_bad_answer stage=... code=BAD_COMPARE`,
`vision_rejected code=NOT_IMAGE`). No identifiers, bytes, hashes, base64, keys or model text.
Spend cap v2 adds `spend_cap_error code=BEGIN` (a request mark could not be written: nothing
sent), `spend_cap_orphan hold=<id> day=<day> usd=<usd>` at startup, and the cap's own
`spend_cap_error code=...` lines with a hold id and amount; hold ids are random, not user data.

### Verdict feedback (WP-UX-02)

Under each verdict the bot offers a button "the verdict is wrong". A press asks for one difference
code (`color`, `size`, `logo`, `model`, `other`; `FEEDBACK_CODES` in `app/photo_checks.py`). The
press is accepted once, for the user's last verdict only, within 24 hours, and is kept in memory
only. One accepted code logs one line: `verdict_feedback a=<A status> diff=<code>`. No user,
chat, token or file id and no free text is logged or stored. A refused press (no verdict, wrong
token, already reported, older than 24 hours, unknown code) gets the `feedback_refused` text and
no log line.

### Per-attribute codes (WP-CONTROLS-01/02/04, D-8, D-10)

One line per completed check (`assess` returned; never for a refusal or UNAVAILABLE), logged by
`compare` in `app/vision_compare.py`:
`check_codes v=3 a=<A' status> n=<attributes> codes=<compact>`. `a` is the `assess` status.
Enum codes and small integers only, alphabet `[0-9A-Za-z,;]`, `codes` at most 160 characters
(12 attributes use about 115): no claim, label, observation, attribute_id, roi value, user,
chat or file id, no hash. The result of `assess` is unchanged. If the line cannot be built the
log gets `check_codes_error code=RECORD` and the check goes on.

`codes` = one token per attribute in inventory order, joined by `,`, then `;` and the extra code.
Token = `<pos><outcome><ev1><ev2><fine><tlc><mm><class>`:

| Field | Codes |
|-------|-------|
| `pos` | 1.. (inventory position) |
| `outcome` (T2, both orders) | `M` MATCH, `X` MISMATCH, `N` NEEDS_REVIEW, `A` ASYMMETRIC_PASS, `V` NOT_VISIBLE_IN_REFERENCE |
| `ev1`, `ev2` (evidence, first and second order) | `D` DIRECT, `P` PARTIAL, `N` NONE |
| `fine` | `1` reference box area < `FINE_ROI_AREA`, else `0` |
| `tlc` (v=2) | `1` the claim names a text, a logo or a count (`vision_core.text_logo_count`), else `0` |
| `mm` (v=2) | `1` the verdict was MATCH in both orders (the outcome alone cannot tell MATCH from MISMATCH when the evidence is PARTIAL), else `0` |
| `class` (final, used by rule A') | `C` confirmed, `D` differs, `V` not_visible, `O` orders_disagree, `W` weak_evidence, `F` fine_detail_unverified, `H` (v=3) out_of_view: not compared |
| extra (after `;`) | `n` none, `u` unconfirmed, `c` confirmed |

Example: `check_codes v=3 a=MATCH n=4 codes=1MDD001C,2MDD001C,3MDD001C,4NNN110H;n` (three
confirmed, the fourth out of view in both orders).
Version 3 (WP-CONTROLS-04) has the token of version 2 plus the class letter `H`, and its `a=` is
rule A'; the views themselves are not recorded. Version 2 (WP-CONTROLS-02) has no `H` and its
`a=` is rule A. Version 1 (WP-CONTROLS-01) had no `tlc` and no `mm`:
`<pos><outcome><ev1><ev2><fine><class>`. The recompute reads all three (rule B is n/a for v=1).

The text/logo/count flag (WP-CONTROLS-02, D-2 option (a), D-5) is a runtime keyword heuristic
over the inventory claim, boolean only: `vision_core.TEXT_LOGO_COUNT_RULE` (sha256 of its
canonical JSON `2ca36df28ba6edba6da3befeacc00f4a68f84504e473500541d3f07c54d40eeb`, pinned in
`tests/test_vision_controls_flag.py`) and `text_logo_count(claim)`. The claim is model text: it is
used in memory to compute the boolean and is never stored, logged or printed. Known miss
(backlog): `двойной шов` is not flagged. The flag only feeds rule B in the recompute; the production
verdict is rule A' (rule A before v=3).

`python3 -m app.vision_controls MANIFEST --codes FILE` writes the same line per run, prefixed by
`kind=same|recrop|different` (`kind=<kind> unavailable` for a run without a record), no image path.
`python3 -m app.recompute FILE [FILE ...]` reads such files or bot logs and recomputes:
rule A' (the production rule since v=3, counted on v=3 lines only; must equal their recorded
`a=`), rule A (the rule before D-10 on every line, `H` counted as not_visible; must equal the
recorded `a=` of a v=1 / v=2 line; on v=3 lines it stands next to A' and shows what D-10
changed), rule B (a coarse attribute, not fine and not text/logo/count, counts as confirmed with
MATCH in both orders and evidence PARTIAL or better; v=2 and v=3 lines, v=1 lines are counted and
left out), rule C (the first 8 inventory attributes). B and C are built on the production rule
of the line (A' for v=3, A for v=1 / v=2: an old line keeps the numbers it had) and keep the
extra-element outcome. A recorded status that differs from the production rule of its version
gives exit 1 with the count ("recorded A differs from recomputed A").
Per rule it prints the positive MATCH share (lines with a kind; UNAVAILABLE counts against), the
false MATCH count on `different`, the NEEDS_REVIEW share and the line count; shares have one
decimal, rounded half up; never a line's content. A `check_codes_error` line is skipped and
counted separately; a class letter that contradicts its outcome/evidence/fine is counted and
gives exit 1, like an unparsable line or a recorded A that differs (`H` is consistent only with
evidence NONE in both orders, and only on a v=3 line).

### Durable `codes.log` (WP-UX-05, D-9)

A redeploy recreates the container and `docker logs` restarts, so `app/codes_log.py` (a
`logging.Handler` attached in `app/bot.py`, nothing changed in `vision_compare.py`) appends the
two code kinds to `DATA_DIR/codes.log` (default `/data/codes.log`):
`<YYYY-MM-DD UTC> check_codes v=3 a=<STATUS> n=<N> codes=<compact>` and
`<YYYY-MM-DD UTC> verdict_feedback a=<MATCH|MISMATCH|NEEDS_REVIEW> diff=<color|size|logo|model|other>`.
Date only, no time of day. A record is written only when its message is exactly one of these
formats (`check_codes` v=1, v=2 and v=3 are accepted and also parsed with the recompute parser, so a forged line is dropped);
every other record (`vision_call`, `check_done`, free text, an id) is dropped.

- File: created 0600, appended with `O_APPEND`, one line per record. A write failure logs
  `codes_log_error code=WRITE` (once until a write succeeds again) and never breaks the bot.
- Retention: at startup and once per UTC day (the polling loop's tick) the file is rewritten
  atomically (temp, fsync, replace, 0600; an existing file with another mode is chmod-ed to 0600 at startup) keeping lines dated within the last 90 days, at most
  5 MiB (oldest lines dropped first). A failed rotation logs `codes_log_error code=ROTATE` and
  is retried after an hour. One process writes it (as the spend cap).
- Privacy: no user, chat, file or marker id and no model text, so the file has no link to a
  user; `/forget` does not touch it. The bot's privacy text names it ("and anonymised check
  codes not linked to you", ru and en).
- Reading: `python3 -m app.recompute /data/codes.log` (the date prefix is fine: the recompute
  finds `check_codes v=...` anywhere in a line).

### Format-reject diagnostics (WP-BOT-15)

A format reject (`BAD_INVENTORY`, `BAD_COMPARE`) logs which rule failed:
`vision_bad_answer stage=<stage> code=BAD_INVENTORY|BAD_COMPARE rule=<RULE> [path=<path>] [count=<n>] [missing=<a,b>]`.
`app/answer_rules.py` classifies the T2 `ContractError` by our own fixed message templates and
never prints the message (it embeds model values). `path` is the schema path with indices and
our field names only (`attributes[3].reference_roi`, `results[1]`); a field name the model
invented is never printed (`FIELDS_UNKNOWN` carries `count` only); `missing` lists our own
field names (`FIELDS_MISSING`). Anything that does not match a template is `OTHER`. An
`AnswerError` without a rule (`NOT_JSON`, `CUT_OFF`, ...) keeps the old line. The user-facing
behaviour (UNAVAILABLE, not charged) and the T2 contract are unchanged.

`python3 -m app.vision_controls` prints `rejects by stage x rule:` with `stage=... rule=... n=...`
lines (fixed codes and counts, no paths, no model text); under `--gate` the table comes before
the PASS/FAIL line.

Repairs before validation (D-20), logged as `vision_answer_fixed stage=<stage> rule=<RULE> [n=<count>]` (codes and counts only):

- `ROI_CLAMPED`: a box with x+w > 1000 or y+h > 1000 (x, y in [0, 999], w, h >= 1) gets w = min(w, 1000-x), h = min(h, 1000-y) in `app/vision_core.py` before T2 validation (inventory `reference_roi`; compare `reference_roi`, `candidate_roi`, unmapped `candidate_roi`). x or y outside the frame, or an area < 100 after clamping, is rejected by T2 as before. The clamped inventory is what is hashed and sent to the compare calls. A clamped reference box is smaller, so it may cross `FINE_ROI_AREA`: that only changes the reason label of an attribute already under review (`fine_detail_unverified`), never the status.
- `TRAILING_TAIL`: `extract_answer` parses the first complete JSON object; a tail made only of whitespace and `}` `]` backtick is accepted. Any other tail stays `NOT_JSON`.
- `VIEW_DEFAULTED` (WP-CONTROLS-04, compare stages only, `n` = results): a compare result without a valid `candidate_view` was read as `IN_VIEW`. Not a format problem of the T2 answer; it shows a model that ignores the new field (every detail then stays comparable, as before D-10).

- `VIEW_VERDICT_FIXED` and `VIEW_ROI_FILLED` (WP-CONTROLS-04 REVISE 2, compare stages only, `n` = results): repairs of a result whose `candidate_view` is exactly `OUT_OF_VIEW`, made after the view is popped and before the clamp and T2 validation. For a detail the candidate cannot show, the model sometimes answers MATCH with evidence NONE or gives no `candidate_roi`; T2 refuses both and the check ended UNAVAILABLE. (a) `MATCH` + `NONE` becomes `MISMATCH` + `NONE` (the prompt's own convention for a hidden detail); only this exact pair. (b) Then, with evidence `NONE` and a `candidate_roi` that is not four integers, the box becomes the result's `reference_roi` when that is four integers, else `[0, 0, 1000, 1000]`. Never for `IN_VIEW`, never for PARTIAL or DIRECT evidence, never `reference_roi`. A repaired result has evidence NONE, so it can only end as `not_visible` or `out_of_view`, never confirmed.

The controls table also prints `repaired answers by stage x rule:` with these five counts (once per call).

| Rule | Meaning |
|------|---------|
| `WRAPPER_KEYS` | inventory answer keys are not exactly {attributes} |
| `NO_ATTRIBUTES` | attributes is not a list, or it is empty |
| `TOO_MANY_ATTRIBUTES` | more attributes than MAX_ATTRIBUTES |
| `NOT_OBJECT` | an object was expected (attribute, result, element, response) |
| `SCHEMA_VERSION` | schema_version is not the expected one |
| `REF_ID` | reference_photo_id is not the expected one |
| `FIELDS_MISSING` | a required field is missing (our field names printed) |
| `FIELDS_UNKNOWN` | an unknown field is present (count only, never the name) |
| `TEXT_LENGTH` | a text field is not a string, blank, or too long |
| `SHA_FORMAT` | a sha256 field is not a lowercase hex digest |
| `ROI_SHAPE` | a roi is not four integers |
| `ROI_OUT_OF_FRAME` | a roi is outside the frame or smaller than 100 |
| `DUP_ATTR` | duplicate attribute_id in the inventory |
| `SHA_MISMATCH` | inventory_sha256 is not the pinned inventory |
| `RESULTS_NOT_LIST` | results or unmapped_candidate_elements is not a list |
| `ATTR_UNKNOWN_OR_DUP` | a result names an unknown or repeated attribute_id |
| `VERDICT_EVIDENCE` | verdict or evidence_quality invalid, or contradictory together |
| `ROI_NOT_NULL` | reference_roi must be null for NOT_VISIBLE_IN_REFERENCE |
| `RESULTS_MISSING` | an inventory attribute has no result |
| `VISIBILITY_VALUE` | visibility_in_reference is not an allowed value |
| `NOT_CANONICAL` | the answer is not canonical JSON (NaN, Infinity, odd types) |
| `EXPECTED_IDS` | internal: no expected attribute ids |
| `OTHER` | no known template matched |

## Cost per check (estimate, not measured)

Three calls. Assumptions (`vision_compare.ASSUMED`): 1,100 tokens per image, inventory
instruction 300, compare instruction plus inventory 900, inventory answer 400 tokens,
each compare answer 900 tokens.

- inventory: 1,400 prompt, 400 completion
- each compare: 3,100 prompt (two images), 900 completion; two of them
- total: about 7,600 prompt and 2,200 completion tokens

`estimate_check_cost(prompt_usd_per_m, output_usd_per_m)` computes the dollars. At
illustrative prices of 0.30 / 2.50 USD per million tokens (not the real price of any
model): 7,600 x 0.30 / 1e6 + 2,200 x 2.50 / 1e6 = about 0.0078 USD per check, roughly
three times the cost of a single call. The real cost comes from `usage.cost` of each
reply (requested via `usage.include`) and is logged as `cost=` on the `vision_call` lines;
the spend cap sums those. The smoke test's lines replace the assumptions above. Latency is also three sequential calls.

## Spend cap

`app/spend_cap.py` (v2.1.1, fable-terminal, D-17; settle-once rule by theone, WP-CONTROLS-06)
keeps a per-UTC-day ledger in `DATA_DIR`,
written atomically (file and directory fsync): `{"day", "spent_usd", "holds", "memo"}`, where
`holds` maps a random hold id to `{"day", "usd", "in_flight", "at"}` (`at`: when the hold was
taken, Unix seconds) and `memo` maps the id of a hold that is gone to `{"k", "usd", "at"}`
(`k`: `released`, `expired` or `settled`; `usd`: the highest cost settled for it; `at`: when
it was last remembered); no user identifiers. The cap
is `VISION_DAILY_CAP_USD` (default 2.00; an invalid value fails closed). A check reserves its
worst case (3 x 0.02 USD) before the first provider call; a call with no usable cost counts as
a fallback of 0.02 USD. One process per ledger: a second one fails to take the lock
(`SECOND_WRITER`). Wired into the bot by WP-BOT-05 and WP-BOT-12 (see Wiring above).

v2 semantics (v2.1.1):

- `reserve()` writes the hold to the ledger before it returns; `begin()` writes "one request
  in flight" before each send. A request never leaves before its liability is on disk.
- A hold is freed only by evidence: `record(cost)` books a real charge, `not_sent()` says the
  request provably never left, `close()` frees the rest only when nothing is in flight.
- A hold found on the ledger at startup belongs to a process that died (or closed with a
  request out). It is an orphan: logged as `spend_cap_orphan hold=<id> day=<day> usd=<usd>`,
  counted in full for its day, never released automatically. It stops counting only when its
  day is over AND it is older than `orphan_lifetime_s` (default 600 s, at least 2 x the 30 s
  client timeout; the bot uses the default), so a request still billing across midnight keeps
  its hold.
- A v1 ledger (`{"day", "spent_usd"}`) is read as having no holds, with its total kept; a v2
  hold without `at` is read as taken at the end of its day. A ledger without `memo` (v1, v2,
  v2.1.1) is read as remembering nothing: every earlier id is `unknown`. The first write adds
  `"memo"`; there is no manual step.
- A hold is settled once (WP-CONTROLS-06). The ledger remembers the ids of holds that were
  released (closed with nothing in flight), that expired as orphans, or that were settled, in
  the same file, written by the same atomic write under the same lock. An id is remembered for
  14 days since it was last remembered (`settle_memory_s`) and at most 2000 ids are kept
  (`settle_memory_max`; the oldest are forgotten first); the bot uses the defaults. A forgotten
  id is `unknown` again and books in full: the error is always on the side of counting too
  much. With 2000 remembered ids the ledger is about 175 kB; the eight ledger writes of one
  check then took about 0.13 s on the build machine (0.06 s with an empty memory).
- Fails closed (no provider call): an unreadable or changed ledger, a clock that went back past
  the ledger's day, a failed write. `cap.state` is `ready` or `failed`, `cap.fail_code` says
  why (`LEDGER_CORRUPT`, `LOCK_WITHOUT_LEDGER`, `LEDGER_CHANGED`, `LEDGER_UNREADABLE`,
  `CLOCK_BACKWARDS`, `CLOSED`). If it is not `ready` at startup the bot logs
  `vision disabled, code=<fail_code>` and runs without checks.

Orphan recovery (operator). Look up the real cost of the requests made around the orphan's
time in the OpenRouter activity log, then settle it with the bot stopped (the bot holds the
lock), on the VPS in `~/app`, with the id from the log line and the cost in USD:

```bash
docker compose stop bot && docker compose run --rm bot python -c "from app.spend_cap import SpendCap; c = SpendCap('/data/spend_cap.json', 0); print(c.settle_line('HOLD_ID', REAL_COST_USD)); c.close()" && docker compose start bot
```

What it prints: the answer, and after `settled`, `repeat` or `expired` the highest cost the
ledger now remembers for that id (`SpendCap.settle_line`, board patch P7, glitchfox; asked by
nautilus). Settling one id with 0.01, then 0.03, then 0.02 prints `settled 0.01`, `repeat 0.03`,
`repeat 0.03`: the third line says the ledger still holds 0.03, not that 0.02 was booked. The
other answers print alone; `unknown` always stays bare, although its cost is booked: the call
could not confirm what the ledger did. If the ledger does not remember the id after the settle
(with `settle_memory_max=0` it remembers nothing), `settled`, `repeat` and `expired` print alone
too. A bare answer means no remembered amount, never a zero cost: a remembered zero prints
`settled 0` (review of P7 by gpb-agent-7a28a9f720). The answers themselves:

- `settled`: the orphan is dropped and the cost booked.
- `repeat`: this id was settled before. Nothing is booked, unless the new cost is higher than
  the highest one settled so far: then only the difference is booked.
- `released`: the hold ended normally (the check finished and closed it). Nothing is booked.
- `expired`: the orphan had already expired. The cost is booked to today, once.
- `unknown`: the id was never seen, or it was forgotten (14 days after it was last remembered,
  or pushed out by 2000 newer ids). The cost is booked in full.
- `live`: the hold belongs to a running check. Nothing is booked.
- `invalid`: the id is not a string, or is the empty string. Nothing is booked and no state,
  clock or ledger access occurs. Retype the command with the correct id.

Every non-empty string is a valid id, including `" "`, `"5"` and `"ab"`; there is no 32-hex
format requirement. An unfamiliar non-empty string follows `unknown` and books in full, so
copy the actual hold id from the log rather than guessing it.

Running the same command twice is safe. A cost typed too low is corrected by settling again
with the right total. A cost typed too high cannot be lowered: it over-counts until the day
ends. Not settling is safe too: the orphan blocks only its own day's budget.

The clock is read only when an operation runs, so an expired orphan is remembered from the
first operation after it expired, not from the second it expired (theone's finding, board msg
58bd3f89). With 14 days of memory against a 600 s orphan lifetime this matters only for an id
touched twice 14 days apart: it is then `unknown` and books in full.

Dry run of the settle rule, without spending: a temp ledger inside a one-off container, no
provider call, the live ledger and the running bot untouched:

```bash
docker compose run --rm bot python -c "import json, os; from app.spend_cap import SpendCap; os.makedirs('/tmp/dry'); p = '/tmp/dry/spend_cap.json'; c = SpendCap(p, 1); r = c.reserve(); r.begin(); c.close(); c = SpendCap(p, 1); print(*[c.settle(r.hold_id, 0.01) for _ in range(3)]); c.close(); d = json.load(open(p)); print(d['spent_usd'], len(d['memo']))"
```

It prints `settled repeat repeat` and `0.01 1`.

Rollback. Fix forward is the default. The revision before this one (WP-CONTROLS-07) reads a
ledger with `memo` and keeps the key untouched: a rollback to it needs no step, and while it
runs nothing new is remembered (those ids are `unknown` afterwards and book in full). Any
revision older than WP-CONTROLS-07 calls such a ledger corrupt and runs with vision off
(`vision disabled, code=LEDGER_CORRUPT`). Before starting one, stop the bot and strip the key
(atomic, mode 0600; it prints the keys that are left, `['day', 'holds', 'spent_usd']`):

```bash
docker compose stop bot && docker compose run --rm bot python -c "import json, os; p = '/data/spend_cap.json'; d = json.load(open(p)); d.pop('memo', None); fd = os.open(p + '.strip', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.fchmod(fd, 0o600); os.write(fd, json.dumps(d).encode()); os.fsync(fd); os.close(fd); os.replace(p + '.strip', p); print(sorted(d))"
```

Then start the old revision. Stripping forgets what was settled: a second settle of the same
id books again, as it did before WP-CONTROLS-06.

Lock without ledger = vision off. If `DATA_DIR` has `spend_cap.json.lock` but no
`spend_cap.json` (the ledger was deleted, or a v1 volume where no cost was ever recorded), the
cap cannot tell what was spent: the bot logs `vision disabled, code=LOCK_WITHOUT_LEDGER` and
runs without checks. A corrupt ledger logs `vision disabled, code=LEDGER_CORRUPT` and is left
untouched for a human to look at. To reset, stop the bot and remove both files
(`spend_cap.json` and `spend_cap.json.lock`); this forgets today's spend.

Post-deploy check: the log has `vision enabled`; note any `spend_cap_orphan` lines;
`cat /data/spend_cap.json` shows `"holds": {}` while no check is running, and a `"memo"`
key from the first write on (one `released` entry per finished check).

## Per-call cost ceiling (WP-BOT-12 criterion 4)

Credit: gpb-agent-7a28a9f720 and zenith-claude (board ae90ed2b). The hold of 0.02 USD per call
is a real ceiling only if one call cannot cost more. The constants and their arithmetic are in
`app/vision_request.py`; `tests/test_call_ceiling.py` recomputes them.

- Pricing basis (pinned): OpenRouter `/api/v1/models`, `google/gemini-3.6-flash`, 09.10.2026:
  input (prompt and images) 0.75 USD/M tokens, output (completion and internal reasoning)
  3.75 USD/M. The test fails when `VISION_MODEL` in `deploy.yml` is another model.
- Output: `max_tokens: 2800` in every request. On this route it bounds all completion tokens,
  reasoning included (live check, 09.10.2026, `max_tokens=40`: effort minimal and effort high
  both ended `finish_reason=length` with 36 completion tokens, 34 of them reasoning in the
  second, same cost). A cut-off reply (`finish_reason: "length"`) is billed: its cost (or the
  0.02 fallback) is recorded and the check ends UNAVAILABLE (`code=CUT_OFF`), not charged.
- Images: longest side at most 1536 px, read from the file header (`app/intake/sniff.py`),
  nothing re-encoded. For a Telegram photo the bot takes the largest size variant within the
  limit (normally Telegram's 1280 px one); an image sent as a document with a longer side, or
  with a header that cannot be read, is refused before any call (`vision_rejected
  code=SIDE_TOO_LONG` / `NO_SIZE`, the check is UNAVAILABLE and not charged). Instead of the
  generic UNAVAILABLE text the seller gets one line saying what to do: `image_too_large`
  («Фото слишком большое для проверки — пришлите его как фото, а не файлом.»; also for a file
  over the 8 MiB byte cap, whether Telegram declares the size or it only shows while streaming:
  the download raises `TooLarge` and `app/photo_checks.py` answers with this line) or
  `image_unreadable` («Не могу прочитать это изображение — пришлите его
  как фото.»), ru/en in `app/bot_texts.json`. Worst case per
  image: 3,096 tokens (Gemini 2.x tiling at 1536 x 385; Gemini 3 "high" is 1,120). This is a
  planning bound from public rules, not measured on this model: the `usage_prompt` numbers of
  the `vision_call` lines show the real count.
- Text: at most 6,000 UTF-8 bytes of prompt per request (`PROMPT_TOO_LONG` otherwise; today's
  largest is about 5,200 since WP-CONTROLS-04), at most two images; one token covers at least one byte.
- Worst case of one call: (2 x 3,096 + 6,000 + 300 overhead) x 0.75 / 1e6 + 2,800 x 3.75 / 1e6
  = 0.019869 USD, within the 0.02 hold.

The image limit changes what the model sees: a Telegram photo that also comes in a larger
variant (Telegram's 2560 px one) is now checked at 1280 px, and documents above 1536 px are
refused. The WP-BOT-07 controls must run (or be re-run) on the code after this change.

## Privacy note for the bot text

Photos are sent to the configured vision provider (currently OpenRouter and the model
behind it) for the check. The bot does not store them: they are deleted when the check
ends. Every request carries `provider.data_collection = "deny"`, so only providers that
do not store or train on the request are used; their retention is otherwise governed by
their terms.

## Waitlist and /forget (WP-BOT-09, D-14)

`app/waitlist.py` keeps `DATA_DIR/waitlist.json`: `{"users": {"<telegram id>": {"source",
"joined_at"}}}`, `joined_at` = UTC date of the first /start. The raw id is kept on purpose: the
list serves the public 50% offer to the first 100 users (issue #17).

- Retention: a row is deleted 180 days after `joined_at`, or 30 days after paid checks launch
  (`PAID_LAUNCH_DATE`, ISO date such as `2027-01-15`; unset or invalid = not launched, an invalid
  value logs `waitlist_error code=BAD_PAID_LAUNCH_DATE`), whichever comes first. The purge runs at
  startup and once per UTC day from the polling loop (a failed purge is retried after an hour).
- Writes: always atomic and 0600 (temp file in the same directory, fsync, `os.replace`).
- Migration at startup, idempotent: old rows `"<id>": "<source>"` become `{"source",
  "joined_at"}`; a missing `joined_at` is the UTC date of the file's mtime (today if the mtime is
  unavailable). The mtime is the file's latest write, never earlier than any join, so this never
  shortens anyone's retention. Ids and sources stay exactly as they were, the file mode becomes
  0600, and no backup copy is made (it would survive /forget). Both row formats are read.
  Temp files left by an interrupted write (`waitlist.json.tmp`, `.waitlist.*.tmp`) are removed
  at startup for the same reason.
- Corrupt or unreadable file: never overwritten. Each access logs `waitlist_error code=CORRUPT`
  (no content); /start still answers but records nothing, totals are zero, /forget answers that it
  could not finish. Fix or remove the file by hand to recover.

`/forget` (ru/en reply, works with the photo check on or off) deletes the user's waitlist row,
their free-check counter row (`CheckCounter.forget`, HMAC key) and any half pair held in memory.
With the photo check off for any reason, the counter row is deleted through a standalone
`CheckCounter` when `CHECK_COUNTER_SALT` is valid and `DATA_DIR/check_counter.json` exists; a
corrupt counter file makes /forget answer that it could not finish. Each file is rewritten atomically; a repeated /forget is fine.
Logs: `forget done`, or `forget_error code=WAITLIST_CORRUPT|WAITLIST_WRITE|COUNTER`, never an id.
Without `CHECK_COUNTER_SALT` the counter key cannot be computed, so only the waitlist row goes.

## Live smoke test (one pair, orchestrator)

```
VISION_BASE_URL=https://openrouter.ai/api/v1/chat/completions \
VISION_MODEL=google/gemini-3.6-flash \
VISION_HOST_ALLOWLIST=openrouter.ai \
VISION_API_KEY=... \
python3 -m app.vision_compare /path/reference.jpg /path/candidate.jpg
```

Run from the repo root. Prints the status, plus three `vision_call` log lines; exit
code 0 for MATCH/MISMATCH/NEEDS_REVIEW, 1 for UNAVAILABLE, 2 for bad usage or missing
config. Uses the real T3b `call_provider`; needs `core_t2/` and `core_t3/` on the path (run from the repo root).

## Tests

`python3 -m unittest discover -s tests -t .` (fake transports only; every chain test
runs through the real `core_t3` `call_provider`, and a spy test asserts it is called once
per stage). `tests/test_image_layout.py` rebuilds `/app` from the Dockerfile's `COPY`
lines and imports the chain there.
`tests/test_vision_controls_codes.py` and `tests/test_recompute.py` cover the per-attribute codes and the recompute (WP-CONTROLS-01);
`tests/test_vision_controls_flag.py` covers the text/logo/count flag, code record v=2 and rule B (WP-CONTROLS-02).
`tests/test_out_of_view.py` covers `candidate_view`, the out_of_view class, rule A', the notes, the owner's case of 11.10 and
the comparison with rule A on random answers; `tests/test_recompute_v3.py` the v=3 record in the recompute;
`tests/test_vision_controls_view.py` the controls run (WP-CONTROLS-04).
`tests/test_vision_controls_zoom.py` covers the zoom experiment (WP-CONTROLS-05): targets, the measured rule, bad answers,
the crop geometry with a fake imaging library (and the real one where Pillow is installed), the zoom call ceiling.
`tests/test_call_ceiling.py` covers the per-call ceiling (WP-BOT-12 criterion 4) and
`tests/test_spend_cap_v2_wiring.py` the spend cap v2 wiring (begin, not_sent, a process
killed between begin and record, v1 ledger, orphan settle, settle closing the reservation).
`tests/test_settle_once.py` covers the settle-once rule (WP-CONTROLS-06): the four traces
recorded from the module at 18d6caa give the required answers, and seeded random traces through
the real `SpendCap` and through the pure model `tests/settle_once_model.py` (theone's accepted
entry of board task S1, byte for byte; the bot does not import it) agree after every step.
Spend cap mutants (fable-terminal; not run by CI, each must print `killed`):
`python3 tests/mutation/spend_cap_mutants.py`.
