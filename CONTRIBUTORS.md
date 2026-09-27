# Contributors

## Code and tests

| Who | Contribution | Since |
|---|---|---|
| LookTwinStudio (ITSalt) | owner, infrastructure, review | 2026-09-24 |
| looktwin-feedback (studio's assistant agent) | setup, landing page, source counter, security review | 2026-09-24 |
| zenith-claude | verdict contract tests (`tests/test_verdict_contract.py`, PR #4) | 2026-09-24 |
| daedalus-protocore | T1c image header sniffing (`app/intake/sniff.py`); T1d streaming download (`app/intake/download.py`) and its extra tests (`tests/contract/test_t1d_window.py`, `test_t1d_beyond.py`) | 2026-09-25 |
| antigravity | T1a Telegram update parsing (`app/intake/parse.py`); L10 reply language (`app/lang.py`) | 2026-09-26 |
| xboss-xoxomo | T1f seller-facing verdict text (`app/verdict/format.py`); T1b photo pairing (`app/intake/pairing.py`) | 2026-09-26 |
| hermes-scout-42 | T1e temporary photo cleanup (`app/intake/tempfiles.py`) | 2026-09-27 |

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
