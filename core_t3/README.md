# T3: from the comparison core to a live check in the bot

The contract and public tests are by **praktik** (task `photo-checker-t3-contract-v1`, revision v1, getpostingboard.dev thread `86b24834`, post `a2ead20e`). The files in `photo_compare_t3_contract_tests_public/` are byte-identical to the published bundle (tar+gzip SHA-256 `f7fcef9f…ca060d6`).

Implementers add `core_t3/photo_compare_t3_candidate.py`. The public suite must pass 8/8. The order is:

- **T3a** — bot wiring on a fake comparison callable: one call, cleanup always, and `UNAVAILABLE` instead of a guessed verdict.
- **T3b** — provider adapter behind a fake transport. The key comes only from `VISION_API_KEY`. The provider and model are not chosen yet.
- **T3c** — blind 40-row evaluation ledger, reporting three primary counts and no accuracy percentage.

The provider and model are chosen by the owner after T3c. Tests changes go in a separate reply and are never bundled with an implementation.
