# T4: vision transport and request glue

Modules in `app/`:

- `vision_transport.py`: `make_transport(base_url, timeout_cap, host_allowlist=...)`.
  HTTPS only, host allowlist, no redirects (3xx is returned as a status, not followed),
  no environment proxies, 1 MiB response cap. `TimeoutError` passes through for T3b.
- `vision_request.py`: image hygiene (JPEG/PNG/WebP by magic bytes, 8 MiB cap each,
  nothing re-encoded), the inventory request, and extraction of the model's JSON answer.
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
   `call_provider` (T3b).

Status: `MISMATCH` if any attribute is a both-order DIRECT mismatch or an unmapped
candidate element is confirmed in both orders; `MATCH` only if every attribute is a
both-order DIRECT match and neither order reported an unmapped element; otherwise
`NEEDS_REVIEW` (weak evidence, order disagreement, not visible in reference, one-sided
or occluded extras). Any provider failure or unusable answer is `UNAVAILABLE`.
The product-level roll-up (`vision_core.rollup`) is the one piece of policy that T2 does
not define; it is a decision for the strategist. Crop escalation (`escalations` in the
T2 output) is not run in this cut; everything that would escalate is already
`NEEDS_REVIEW`.

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `VISION_BASE_URL` | yes | full chat-completions URL, https, e.g. `https://openrouter.ai/api/v1/chat/completions` |
| `VISION_MODEL` | yes | e.g. `google/gemini-3.6-flash` (`[A-Za-z0-9._:/-]`, up to 100) |
| `VISION_HOST_ALLOWLIST` | yes | comma-separated hosts; the URL host must be listed, e.g. `openrouter.ai` |
| `VISION_API_KEY` | yes | read only by `call_provider` (T3b); never logged |
| `VISION_TIMEOUT` | no | seconds, `0 < t <= 120`, default 30 |

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

`session_factory` is still host-provided (T3a). Until the real `call_provider` replaces
the `NotImplementedError` stub in `core_t3/`, a configured bot answers UNAVAILABLE.
If `core_t2/` is not importable (current Dockerfile copies `app/` only), the feature stays off.

## Logging

Per provider call (three per check), from the provider `usage` field when present
(`n/a` otherwise): `vision_call model=... usage_prompt=... usage_completion=... cost=...
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
three times the cost of a single call. The smoke test's `vision_call` lines give the
real numbers; replace the assumptions with them. Latency is also three sequential calls.

## Privacy note for the bot text

Photos are sent to the configured vision provider (currently OpenRouter and the model
behind it) for the check. The bot does not store them: they are deleted when the check
ends. The provider's own retention is governed by its terms and settings.

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
config. Requires the real T3b `call_provider` and `core_t2/` on the path (run from the repo root).

## Tests

`python3 -m unittest discover -s tests -t .` (fake transports; the T3b double lives in
`tests/test_t4_vision.py` only).
