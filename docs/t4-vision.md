# T4: vision transport and request glue

Modules in `app/` (the image ships `app/` only):

- `vision_transport.py`: `make_transport(base_url, timeout_cap, host_allowlist=...)`.
  HTTPS only, host allowlist, no redirects (3xx is returned as a status, not followed),
  no environment proxies, 1 MiB response cap. `TimeoutError` passes through for T3b.
- `vision_request.py`: `build_request` (two base64 data URLs, JPEG/PNG/WebP by magic
  bytes, 8 MiB cap each, nothing re-encoded) and `parse_verdict`.
- `vision_compare.py`: `compare_from_env(environ)` returns the `compare` callable for
  `run_bot_pair`, or `None` when unconfigured (the bot then behaves as before).

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

## Logging

Per check, from the provider `usage` field when present (`n/a` otherwise):
`vision_call model=... usage_prompt=... usage_completion=... cost=...`.
Failures log a public code only (`vision_unavailable code=TIMEOUT`,
`vision_rejected code=NOT_IMAGE`). No identifiers, bytes, hashes, base64, keys or reasons.

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

Run from the repo root. Prints `STATUS reasons`, plus the `vision_call` log line; exit
code 0 for MATCH/MISMATCH/NEEDS_REVIEW, 1 for UNAVAILABLE, 2 for bad usage or missing
config. Requires the real T3b `call_provider`.

## Tests

`python3 -m unittest discover -s tests -t .` (fake transports; the T3b double lives in
`tests/test_t4_vision.py` only).
