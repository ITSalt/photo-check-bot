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
   model answers `photo-compare/2`, echoing the inventory digest. Each answer goes
   through T2 `validate_response`; a bad answer stops the chain (no third call).
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
 "extra": "none" | "unconfirmed" | "confirmed"}
```

- `MISMATCH`: some attribute is a both-order DIRECT mismatch (present in the reference,
  clearly absent or different where it should be visible), or an extra product element is
  confirmed in both orders.
- `MATCH`: every attribute is a both-order DIRECT match and neither order reported an extra.
- `NEEDS_REVIEW`: anything else, with the `to_check` list saying what and why.
- `UNAVAILABLE`: provider failure or an unusable answer.

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

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `VISION_BASE_URL` | yes | full chat-completions URL, https, e.g. `https://openrouter.ai/api/v1/chat/completions` |
| `VISION_MODEL` | yes | e.g. `google/gemini-3.6-flash` (`[A-Za-z0-9._:/-]`, up to 100) |
| `VISION_HOST_ALLOWLIST` | yes | comma-separated hosts; the URL host must be listed, e.g. `openrouter.ai` |
| `VISION_API_KEY` | yes | read only by `call_provider` (T3b); never logged |
| `CHECK_COUNTER_SALT` | yes, for the bot | key of the free-check counter (D-12), at least 32 characters; unset or short keeps the vision check off |
| `VISION_TIMEOUT` | no | seconds, `0 < t <= 120`, default 30; the transport caps each call at it, and T3b asks for 30 s per call, so values above 30 have no effect |

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

`app/spend_cap.py` (v2.1.1, fable-terminal, D-17) keeps a per-UTC-day ledger in `DATA_DIR`,
written atomically (file and directory fsync): `{"day", "spent_usd", "holds"}`, where
`holds` maps a random hold id to `{"day", "usd", "in_flight", "at"}` (`at`: when the hold was
taken, Unix seconds); no user identifiers. The cap
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
  hold without `at` is read as taken at the end of its day.
- Fails closed (no provider call): an unreadable or changed ledger, a clock that went back past
  the ledger's day, a failed write. `cap.state` is `ready` or `failed`, `cap.fail_code` says
  why (`LEDGER_CORRUPT`, `LOCK_WITHOUT_LEDGER`, `LEDGER_CHANGED`, `LEDGER_UNREADABLE`,
  `CLOCK_BACKWARDS`, `CLOSED`). If it is not `ready` at startup the bot logs
  `vision disabled, code=<fail_code>` and runs without checks.

Orphan recovery (operator). Look up the real cost of the requests made around the orphan's
time in the OpenRouter activity log, then settle it with the bot stopped (the bot holds the
lock), on the VPS in `~/app`, with the id from the log line and the cost in USD:

```bash
docker compose stop bot && docker compose run --rm bot python -c "from app.spend_cap import SpendCap; c = SpendCap('/data/spend_cap.json', 0); print(c.settle('HOLD_ID', REAL_COST_USD)); c.close()" && docker compose start bot
```

It prints `settled` (the hold is dropped and the cost booked), `unknown` (no such orphan, e.g.
already expired; the cost is still booked today) or `live`. Not settling is safe: the orphan
blocks only its own day's budget.

Lock without ledger = vision off. If `DATA_DIR` has `spend_cap.json.lock` but no
`spend_cap.json` (the ledger was deleted, or a v1 volume where no cost was ever recorded), the
cap cannot tell what was spent: the bot logs `vision disabled, code=LOCK_WITHOUT_LEDGER` and
runs without checks. A corrupt ledger logs `vision disabled, code=LEDGER_CORRUPT` and is left
untouched for a human to look at. To reset, stop the bot and remove both files
(`spend_cap.json` and `spend_cap.json.lock`); this forgets today's spend.

Post-deploy check: the log has `vision enabled`; note any `spend_cap_orphan` lines;
`cat /data/spend_cap.json` shows `"holds": {}` while no check is running.

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
  («Фото слишком большое для проверки — пришлите его как фото, а не файлом.»; also for the
  8 MiB byte cap) or `image_unreadable` («Не могу прочитать это изображение — пришлите его
  как фото.»), ru/en in `app/bot_texts.json`. Worst case per
  image: 3,096 tokens (Gemini 2.x tiling at 1536 x 385; Gemini 3 "high" is 1,120). This is a
  planning bound from public rules, not measured on this model: the `usage_prompt` numbers of
  the `vision_call` lines show the real count.
- Text: at most 6,000 UTF-8 bytes of prompt per request (`PROMPT_TOO_LONG` otherwise; today's
  largest is about 4,500), at most two images; one token covers at least one byte.
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
`tests/test_call_ceiling.py` covers the per-call ceiling (WP-BOT-12 criterion 4) and
`tests/test_spend_cap_v2_wiring.py` the spend cap v2 wiring (begin, not_sent, a process
killed between begin and record, v1 ledger, orphan settle, settle closing the reservation).
Spend cap mutants (fable-terminal; not run by CI, each must print `killed`):
`python3 tests/mutation/spend_cap_mutants.py`.
