# T3a: session-to-bot boundary

`core_t3/photo_compare_t3_candidate.py` exposes the unchanged v1.1 T3a
contract from praktik. Its implementation lives in `app/photo_compare.py`, so
the existing Dockerfile that copies only `app/` includes the runtime boundary. `run_bot_pair(session, compare, logger)` takes the exact
reference and candidate objects returned by `session.photos()`, calls `compare`
once in that order and returns only the fixed schema/status envelope. It emits
no logs and retains no photo/hash. Acquisition/comparison exceptions or malformed
results become `UNAVAILABLE`. `session.delete_all()` runs in `finally`; cleanup
failure propagates and never returns a verdict.

The three T3a functions in the public suite are the acceptance gate, clarified
by looktwin-feedback in board #66451. `call_provider` (T3b) and
`summarize_blind_eval` (T3c) are explicit `NotImplementedError` stubs. The full
8-test T3 suite therefore reports **3 passing T3a tests and 5 unimplemented
T3b/T3c failures**, not 8/8. The public files have not been edited.

## Bot entry point

The existing `app.bot.handle` and `main` accept optional `photo_checks`. The host
can construct it explicitly:

```python
from app.photo_checks import PhotoChecks
from app.bot import main

# The host supplies these dependencies; no provider is selected by T3a.
checks = PhotoChecks(session_factory, compare)
main(photo_checks=checks)
```

`session_factory(reference_file_id, candidate_file_id)` receives the *real*
photo ID first and the *generated* photo ID second, even when the user sends
them in the opposite order. It returns a session whose `photos()` returns the
byte objects, and whose `delete_all()` owns all cleanup. A factory that raises
must clean up its partial acquisition itself. Download validation, timeouts,
limits and any real T2 comparison facade remain host responsibilities. The T2
module currently exposes primitives, not a complete `compare(bytes, bytes)`
callable; this PR does not invent a model result or select a provider to fill
that gap. Nothing invokes a paid provider by default.

The opt-in handler uses existing T1 parsing and pairing. It ignores group chats,
keeps half-pairs separate per user/chat, supports cancellation/expiry, and bounds
pending file-ID state (default 1024). It downloads no half-pair and stores no
photo bytes itself. On completion it releases the pairing state, runs the
injected boundary and sends a fixed Russian reply *after* cleanup. Private
comparison fields and raw exception text cannot enter the reply. If cleanup
fails, the bot emits only a fixed log message and sends no verdict.

`python -m app.bot` still starts the existing welcome/waitlist shell with photo
checks disabled. Enabling real intake/comparison requires the integrator to
supply the session factory and comparison facade, as well as existing project
quota, global budget, privacy and provider admission gates. No deployment,
provider, new outbound host or production flag is added by this PR.

## Verification

In a separate local test environment, install `pytest==8.4.2` if needed (test
runner only; the application remains standard-library-only):

```sh
PYTHONPATH=core_t3 python -m pytest -q core_t3/photo_compare_t3_contract_tests_public/test_t3a_bot.py
PYTHONPATH=core_t3 python -m pytest -q core_t3/photo_compare_t3_contract_tests_public
PYTHONPATH=core_t2 python -m pytest -q core_t2/photo_compare_t2_contract_tests_public
python -m unittest discover -s tests -v
```

The second command intentionally exposes the five separate unimplemented tasks'
tests. Do not relabel those failures as skips or a successful full T3 run.

Current CI only discovers `tests/` and does **not** collect `core_t3` (existing
issue #35). The integrator's isolated public-contract run remains the T3a gate.
This PR does not modify workflows. The nine author checks in `tests/` additionally
exercise exact object identity, malformed results, acquisition failure,
interleaved users, cancellation/expiry/cap, factory failure, real bot handler
wiring and cleanup-before-send, cleanup failure redaction, and default-disabled
behavior. All comparisons/sessions/Telegram calls are synthetic or injected.
No real seller photo, live Telegram exchange, model quality or independent
acceptance is established by the author's checks.
