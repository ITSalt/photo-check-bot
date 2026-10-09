# T4: vision transport and request glue

Modules in `app/`:

- `vision_transport.py`: `make_transport(base_url, timeout_cap, host_allowlist=...)`.
  HTTPS only, host allowlist, no redirects (3xx is returned as a status, not followed),
  no environment proxies, 1 MiB response cap. `TimeoutError` passes through for T3b.
- `vision_request.py`: image hygiene (JPEG/PNG/WebP by magic bytes, 8 MiB cap each,
  nothing re-encoded), the inventory request, and extraction of the model's JSON answer.
  `chat_payload` builds every request body (inventory and both compares) with
  `temperature: 0`, JSON output, minimal reasoning, `"provider": {"data_collection": "deny"}`
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

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `VISION_BASE_URL` | yes | full chat-completions URL, https, e.g. `https://openrouter.ai/api/v1/chat/completions` |
| `VISION_MODEL` | yes | e.g. `google/gemini-3.6-flash` (`[A-Za-z0-9._:/-]`, up to 100) |
| `VISION_HOST_ALLOWLIST` | yes | comma-separated hosts; the URL host must be listed, e.g. `openrouter.ai` |
| `VISION_API_KEY` | yes | read only by `call_provider` (T3b); never logged |
| `VISION_TIMEOUT` | no | seconds, `0 < t <= 120`, default 30; the transport caps each call at it, and T3b asks for 30 s per call, so values above 30 have no effect |

If a required variable is unset or empty, `compare_from_env` returns `None` and logs
only the names of the unset variables. If values are set but invalid it raises
`VisionConfigError` (no values in the message). Nothing is hardcoded as a default:
provider and model are the owner's choice.

Wiring (host code, after T3b is merged):

```python
import os
from app.vision_compare import compare_from_env
from app.photo_checks import PhotoChecks
from app.bot import main

compare = compare_from_env(os.environ)
main(photo_checks=PhotoChecks(session_factory, compare) if compare else None)
```

`session_factory` is still host-provided (T3a); wiring `app/bot.py` is WP-BOT-05. The
real T3b `call_provider` is on main (#38), so a configured bot makes real calls (earlier
versions of this note described a `NotImplementedError` stub; that is gone). The
Dockerfile ships `core_t2/photo_compare_t2_candidate.py` and
`core_t3/photo_compare_t3_candidate.py` next to `app/` (not the public contract test
folders); CI runs `python -c "import app.vision_compare, core_t2, core_t3"` in the built
image and `tests/test_image_layout.py` checks the same layout from the Dockerfile. If
either core is not importable, the feature stays off.

## Logging

Per provider call (three per check), from the provider `usage` field, which every
request asks for with `"usage": {"include": true}` (`n/a` when a reply lacks it): `vision_call model=... usage_prompt=... usage_completion=... cost=...
stage=inventory|compare_a|compare_b`. Failures log stage and a public code only
(`vision_unavailable stage=compare_a code=TIMEOUT`, `vision_bad_answer stage=... code=BAD_COMPARE`,
`vision_rejected code=NOT_IMAGE`). No identifiers, bytes, hashes, base64, keys or model text.

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

## Privacy note for the bot text

Photos are sent to the configured vision provider (currently OpenRouter and the model
behind it) for the check. The bot does not store them: they are deleted when the check
ends. Every request carries `provider.data_collection = "deny"`, so only providers that
do not store or train on the request are used; their retention is otherwise governed by
their terms.

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
