# photo-check-bot

A Telegram bot for marketplace sellers: send a generated product photo together with a photo of the real item, get back what broke and a concrete fix task. A small free volume per user, then paid.

Domain basis: the 13 failure modes of generated product imagery (getpostingboard.dev #52595) and the studio's acceptance checklist. Measurement of what the checker misses follows the audit design by nadir-codex.

## How we work

1. **Pull requests only.** Nobody pushes to `main`. Work in a branch named `feat/<short-name>` or `fix/<short-name>` and open a PR.
2. **Review.** Every PR is reviewed for security (network calls, secrets handling, new dependencies, CI changes) and for function. Changes to `.github/`, `ops/`, `Dockerfile` and `docker-compose.yml` need the owner's review (see `CODEOWNERS`).
3. **Deploy.** A merge to `main` starts the `deploy` workflow. It waits in the `production` environment until the owner approves it, then deploys to the VPS.
4. **Secrets.** Nobody except the owner sees any secret. Tokens live in the `production` environment and reach the server only at deploy time, as a `.env` file outside the repo.

## Rules for code (short reviews depend on these)

1. **No secrets in code, tests or history.** Configuration comes only from environment variables (see `.env.example`). CI runs gitleaks on every PR.
2. **Declare every outbound host.** List any external host the code talks to in the table below, in the same PR that adds it. Everything else is blocked on the server.
3. **Every new dependency** gets a pinned version and one line in the PR saying why. Prefer the standard library.
4. **User data.** Photos received from users are deleted right after processing. Don't store more about users than the free-quota counter needs (Telegram user id + count). No analytics, no forwarding to third parties except the declared model API.
5. **Limits.** Enforce a per-user free quota and a global request ceiling, so a traffic spike can't run up model costs.
6. **Language.** Code and comments in English; bot messages in Russian first.

## Declared outbound hosts

| Host | Why |
|---|---|
| `api.telegram.org` | Telegram Bot API (long polling, sending messages, downloading user photos) |
| `openrouter.ai` | vision check (`VISION_HOST_ALLOWLIST`, docs/t4-vision.md); photos are sent with data collection denied |

## Run locally

```bash
cp .env.example .env   # fill in a test bot token of your own
docker compose up --build
```

## Terms

See [`TERMS.md`](TERMS.md): the revenue share, how payouts work and code ownership. It's a statement of intent by the studio, not a signed contract. By opening a pull request you agree to these terms.

## T3a integration

The opt-in session/comparison boundary and bot wiring are documented in
[docs/t3a-bot-integration.md](docs/t3a-bot-integration.md). `python -m app.bot` turns
photo checking on only when `VISION_*` and `CHECK_COUNTER_SALT` are configured and the
spend cap starts (docs/t4-vision.md, Wiring); otherwise it stays off.
