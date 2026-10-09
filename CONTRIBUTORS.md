# Contributors

## Code and tests

| Who | Contribution | Since |
|---|---|---|
| LookTwinStudio (ITSalt) | owner, infrastructure, review | 2026-09-24 |
| looktwin-feedback (studio's assistant agent) | setup, landing page, source counter, security review | 2026-09-24 |
| zenith-claude | verdict contract tests (`tests/test_verdict_contract.py`, PR #4) | 2026-09-24 |
| daedalus-protocore | T1c image header sniffing (`app/intake/sniff.py`); T1d streaming download (`app/intake/download.py`) and its extra tests (`tests/contract/test_t1d_window.py`, `test_t1d_beyond.py`); T2c provider adapter (`provider_call`, `ProviderError`); T2d aggregator (`aggregate_responses`, `aggregate_attributes`, `padded_roi`); T2e eval summary (`summarize_eval`), all in `core_t2/photo_compare_t2_candidate.py` | 2026-09-25 |
| antigravity | T1a Telegram update parsing (`app/intake/parse.py`); L10 reply language (`app/lang.py`) | 2026-09-26 |
| xboss-xoxomo | T1f seller-facing verdict text (`app/verdict/format.py`); T1b photo pairing (`app/intake/pairing.py`); T2a comparison contract layer (`core_t2/photo_compare_t2_candidate.py`) | 2026-09-26 |
| hermes-scout-42 | T1e temporary photo cleanup (`app/intake/tempfiles.py`); T2b ordered calls (`build_ordered_calls` in `core_t2/photo_compare_t2_candidate.py`) | 2026-09-27 |
| praktik | T2 comparison-core spec and public contract tests (`core_t2/photo_compare_t2_contract_tests_public/`, PR #25); T3 integration contract tests (`core_t3/photo_compare_t3_contract_tests_public/`) | 2026-09-27 |
| bidmart-agent | T3d demand-test protocol (`docs/demand-test-v1.md`) | 2026-09-28 |
| booterbrod (operator GitHub: ITSalt) | T3a session/compare boundary and opt-in bot wiring; author integration tests (public contract by praktik) | 2026-09-29 |
| agent-board-sobieg | T3b provider adapter (`call_provider`) and T3c «Светофор» (`summarize_blind_eval`) in `core_t3/photo_compare_t3_candidate.py`; studio fix for BaseException escape (mimo R1) on top | 2026-10-09 |
| mimo-chat-agent, praktik, agent-277e27cc-c99 (Nascens) | T3b red-team tests (`tests/test_t3b_gate.py`: BaseException path, decoder path, clean exception object) | 2026-10-09 |
| antigravity | T3b Code Lens v2/v7 and the red-team iteration (`tests/test_t3b_gate.py` base) | 2026-10-09 |
| zenith-claude | controls gate (`app/controls_gate.py`, `tests/test_controls_gate.py`), board thread ae90ed2b | 2026-10-09 |

## Design

Ideas from the getpostingboard.dev threads that shaped the product (see `docs/verdict-design.md`):

| Who | Contribution |
|---|---|
| nadir-codex | three outcomes per detail (`MATCH` / `MISMATCH` / `NOT_VISIBLE_IN_REFERENCE`); blind audit of passed images |
| iohan | "back not shown" case, the two reasons for "can't check", the demo scenario |
| zenith-claude | mask-switch and reverse-control tests, seller wording without false precision |
| hermes-scout-42 | positional bias and `ASYMMETRIC_PASS`, ROI crops |
| okuta | logging both check orders |
| liminal-cartographer | funnel per source tag |
| holo | checker-generator independence; the first synthetic count stand |
| deepseek-33371 | the three-arm stand with a non-model oracle |
| ring-to-rule | `NOT_VERIFIABLE` as a third outcome in board notation |
| small-useful-steps | T1b review: the labelled-pair case the published contract missed (now in `tests/contract/test_t1b_pairing.py`) |
| fable-terminal | daily spend cap (`app/spend_cap.py`, tests), board thread ae90ed2b |
