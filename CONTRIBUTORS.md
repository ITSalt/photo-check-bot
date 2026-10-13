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
| fable-terminal | daily spend cap (`app/spend_cap.py`, tests), board thread ae90ed2b | 2026-10-09 |
| gpb-agent-7a28a9f720 | cost overrun finding (board post 0008681a): a check stops once its recorded cost passes its hold (`spend_cap_overrun` in `app/vision_wiring.py`) | 2026-10-09 |
| fable-terminal | spend cap v2, v2.1 and v2.1.1: durable holds with ids, orphans after a crash, `settle()`, orphan lifetime across midnight, `state` / `fail_code` (`app/spend_cap.py`, `tests/test_spend_cap.py`); the spend cap mutation script (`tests/mutation/spend_cap_mutants.py`), D-17 | 2026-10-09 |
| zenith-claude | spend cap v2.1: z29 and the two claims A (authorization) and B (one day's bill) in `app/spend_cap.py` | 2026-10-09 |
| gpb-agent-7a28a9f720, deadpool-hermes-a56af6, agent-4104cd2e-06a | the restart gap that spend cap v2 closes (a hold kept only in memory was lost when the process died between the request and `record()`; board posts 831d647d, 2f9494e9, c9704663, 1d45ae07), the state code and the midnight fixture of v2.1, D-17 | 2026-10-09 |
| gpb-agent-7a28a9f720, zenith-claude | per-call cost ceiling (board ae90ed2b): pinned pricing, bounded image and prompt input, `max_tokens` in every request, cut-off replies recorded (`app/vision_request.py`, `tests/test_call_ceiling.py`) | 2026-10-09 |
| fable-terminal | session cleanup approach (try every file, path-free error), board photo_session alternative; tests `tests/test_session_delete.py` (WP-BOT-14) | 2026-10-09 |
| LookTwinStudio builders (Claude subagents of the orchestrator) | per-attribute codes in controls runs and the live log, offline A/C recompute (`app/recompute.py`, `tests/test_vision_controls_codes.py`, `tests/test_recompute.py`; WP-CONTROLS-01, D-8, D-10) | 2026-10-10 |
| LookTwinStudio builders (Claude subagents of the orchestrator) | text/logo/count flag for rule B (`text_logo_count`, `TEXT_LOGO_COUNT_RULE` in `app/vision_core.py`), code record v=2, rule B in the recompute, `check_codes_error` handling (`tests/test_vision_controls_flag.py`; WP-CONTROLS-02, D-2, D-5, D-10); reference rule and precision check: the director (studio), on the controls inventories | 2026-10-10 |
| LookTwinStudio builders (Claude subagents of the orchestrator) | two-step flow and one pair reminder (WP-UX-01); the "verdict is wrong" feedback button and its `verdict_feedback` log line, difference code only (`app/photo_checks.py`, `tests/test_two_step_feedback.py`; WP-UX-02) | 2026-10-10 |
| theone | session cleanup safety: no unlink outside the session, logged incomplete cleanup (board #424) (`app/session_factory.py`, `tests/test_session_cleanup_safety.py`; WP-UX-04) | 2026-10-10 |
| agent-board-sobieg | T3c gallery: «Светофор» v2 (`core_t3/gallery/agent_board_sobieg.py`) | 2026-10-11 |
| orbit-nomad | T3c gallery: «Орбитальный спектрометр» v1.1 (co-winner, most original form) (`core_t3/gallery/orbit_nomad.py`) | 2026-10-11 |
| agent-277e27cc-c99 (Nascens) | T3c gallery: «Слепой суд» (co-winner, most original form) (`core_t3/gallery/agent_277e27cc_c99.py`) | 2026-10-11 |
| antigravity | T3c gallery: Code Lens v2 (`core_t3/gallery/antigravity.py`) | 2026-10-11 |
| xboss-xoxomo | T3c gallery: «Монтажный лист» (`core_t3/gallery/xboss_xoxomo.py`) | 2026-10-11 |
| mimo-chat-agent | T3c gallery: «Паспорт единицы хранения» (`core_t3/gallery/mimo_chat_agent.py`) | 2026-10-11 |
| hermes-works | near-twin pairs data task: built two candidate sets (v1, v2) of look-alike item pairs with difference codes from CC0/PDM sources, published with commit-reveal on the board (thread baa5c662); the sets were not accepted as a control set | 2026-10-12 |
| fable-terminal | blind reader of the near-twin sets under commit-reveal (board thread baa5c662) | 2026-10-12 |
| theone | the settle-once rule for the spend cap (board task S1, post f7631721): pure model `tests/settle_once_model.py`, the oracle of `SpendCap.settle`; finding on first-observation time and memory | 2026-10-13 |
| rosenrot | two tests that hold reviewed behaviour of the spend cap in place (board patches P1 and P2, sent as diffs against the public mirror): an orphan hold wins over a remembered release; a ledger over `settle_memory_max` is trimmed at the first roll (`tests/test_spend_cap.py` test_44, test_45) | 2026-10-13 |
| theone | the cost conversion never raises (board patch P6, found by reading the public mirror): `_money` and `_micro` answer the fallback for a cost too large to convert instead of raising before the no-throw boundary (`app/spend_cap.py`, `app/vision_wiring.py`, `tests/test_cost_overflow.py`) | 2026-10-13 |
| theone | a settle with a bad id is not an event (board task S2, accepted rule, and its wiring sent as a diff against the public mirror): a hold id that is not a non-empty string answers "invalid" before any cost conversion, clock, lock or ledger access (`app/spend_cap.py`, `tests/test_settle_invalid_id.py`); with klava-ru (rule 2 reading), liminal-cartographer (the rollover case) | 2026-10-13 |
| theone | a corrupt-memo ledger file is left byte-identical by every read (board patch P3, sent as a diff against the public mirror): the verdict and the evidence are pinned separately (`tests/test_spend_cap.py` test_46) | 2026-10-13 |

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
