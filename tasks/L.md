# L: landing and first-contact tasks

**Why this exists.** The landing (https://itsalt.github.io/photo-check-bot/ru/ and `/en/`) is how we test demand for the idea before the bot works. A panel of six buyer personas reviewed it. The findings are summarized under each task.

Things that came up across the panel:

- **The pain lands.** All six said the headline is about them. None said "not needed".
- **Three blockers came from all six:**
  - the Telegram bot's name ("ContractsForPeople") looks like a bait-and-switch;
  - there is no price;
  - the button says "Check for free", but the page reveals only at the bottom that this is a waitlist.

Every task below is **one post**. Texts are copy only, pictures are self-contained SVG, and L10 is one small module with contract tests. The studio does the wiring, the build, the PR and the deploy.

## How the landing is built now

| Part | Where it lives | Rules |
|---|---|---|
| Texts | `landing/content/ru.json` and `landing/content/en.json`, same structure | `tests/test_landing_content.py` |
| Pictures | `landing/assets/*.svg`, inlined into the page | same test file, `AssetTest` |
| Bot texts | `app/bot_texts.json` | `tests/test_bot_texts.py` |

About the texts:

- An empty string hides its block. So new blocks already have empty slots, and you only fill yours.
- Limits are in characters. There is no HTML and there are no links.

About the pictures:

- Allowed: a root `<svg>` with a `viewBox`, colours through `style="fill:var(--ink)"`, and only local `#id` references.
- Page tokens you can use: `--bg`, `--ink`, `--muted`, `--card`, `--line`, `--accent`, `--soft`. Each token flips in dark mode.
- Every `id` starts with the file name, for example `diff-example-grad`.
- Not allowed: `<script>`, `<style>`, `<image>`, `<a>`, `<foreignObject>`, `on*` attributes, external or `data:` references, a DTD.
- Keep each file under 12 KB so it fits in one post. The hard limit is 40 KB; if you go over 12 KB, post a gist link.

**Each task owns its keys or files and touches nothing else.** That way several people can work at once without conflicts.

## How to submit

Reply in the board thread with `claim Lx`. In the same reply, post:

- **copy tasks:** one JSON code block with only your keys, shaped like `{"ru": {...}, "en": {...}}`;
- **SVG tasks:** the SVG in a code block;
- **L10:** the full module with `IMPLEMENTED = True`.

Run the tests on your side first if you can. The files are in the task gist.

After that, the studio takes over:

1. We merge your part into the files and run the tests.
2. We take screenshots on mobile and desktop.
3. We open the PR, and the owner merges.
4. We credit you in `CONTRIBUTORS.md`.

If two submissions for a copy or picture task both pass, the owner picks one and both are credited.

The terms are in `README.md`: no payment per task; contributors share 50% of net revenue.

## Facts you must not contradict

- **Status.** Today the bot is a waitlist. `/start` records the user and where they came from, and the bot replies with a welcome. The photo check is in development (tasks T1a–T1f).
- **Free checks and price** (owner's decision, 2026-09-26): **3 checks free, then 300 ₽ for a pack of 10 images.** One image means one generated photo checked against the real item. The price is in roubles only; other currencies come with the launch country, so the EN page says "300 RUB" and doesn't invent a dollar rate.
- **Launch offer:** **the first 100 people on the waitlist get 50% off their first pack** (150 ₽ instead of 300 ₽ for the first 10 images; later packs are full price). This is true and we will honour it; you may use it as the reason to join now. Don't turn it into a count of people already signed up: we publish no user numbers we can't show.
- **What we store:**
  - the Telegram user ID;
  - the first source tag from the link;
  - later, a count of checks for the free quota.

  We store no name, username or phone. We store no image hashes (this is a public promise).
- **Photos:**
  - saved to a temporary folder on our server, a VPS in Germany;
  - deleted when the check ends, and a periodic sweep removes anything left over;
  - nobody at the studio views them;
  - we don't use them to train models;
  - for the check, the pair of photos is sent to a vision-model API, and the provider will be named on the privacy page before checks open.
- **Contact** for data questions: mnikitin@itsalt.ru.
- **What the check covers:**
  - It is built for **clothing and jewelry**, which is the studio's domain.
  - Other categories (bags and leather, shoes, home goods) are **not tuned yet**. Say so; don't imply support.
  - **No millimetres, no percentages, no accuracy numbers.** The verdict design forbids false precision.
  - Size can only be described as "proportions compared with the real item".
- **What the verdict looks like** (for mockups):
  - problems first, each with a fix;
  - details the reference photo doesn't show are marked "send a photo that shows …";
  - then a count of matches;
  - no percentages.

  See `docs/verdict-design.md` and T1f in `tasks/T1.md`.
- **Brand.** On the page it is "LookTwinStudio · Photo check". The bot's username `@contractsforpeoplebot` can't change; its display name can (L9).

## Tasks

### L1: honest hero and status (copy, RU and EN)

**Owns:** `hero.cta`, `hero.cta_note`, `hero.price_note`, `status.text`, `status.cta`.

**From the panel.** All six expected a working check from the top button and found a waitlist at the bottom. One called it "exactly the polite lie the headline is about".

**Do:**
- The first button and its note say plainly that this is a waitlist, and what happens after `/start`.
- `price_note` gives the free checks, the price and the launch offer (the facts above).
- Keep the button short and active.

**Accept when:** a reader who sees only the first screen knows it's a waitlist, knows the price, and knows what the bot will send them.

### L2: categories in the cards and the checklist (copy, RU and EN)

**Owns:** `problems.items`, `checks.groups`, `checks.note`.

**From the panel:**
- A jewelry maker had to hunt for jewelry among the clothing items.
- A leather-goods owner saw nothing about bags.
- A card designer asked about home goods.

**Do:**
- Keep 3–4 problem cards, and make at least one of them jewelry, for example a stone's colour or a pair of earrings that aren't a pair.
- Split the checklist into groups ("Одежда" / "Украшения", "Clothing" / "Jewelry").
- `checks.note` says two things:
  - other categories are not tuned yet;
  - the bot highlights differences and the seller decides.
- Rewrite "jewelry size" in line with the facts: no millimetres.

### L3: the before/after illustration (SVG, no words)

**Owns:** `landing/assets/diff-example.svg`, `illustration.alt`, `illustration.caption` (RU and EN).

**From the panel.** Five of six asked for a picture: "not a single image, show me a ring with the stone purple on the left and lilac on the right."

**Do:**
- Two panels side by side. Left is the generated image, right is the real item. Draw a flat illustration, not something that looks like a photo.
- Show 2–3 differences, each circled and numbered. Use a jewelry subject, or one piece of clothing.
- No words inside the SVG, only digits and marks. Labels go into `alt` and `caption`, and the caption says it's an illustration.
- Readable at 360 px wide. Works in light and dark mode through the page tokens.
- Set `illustration.asset` to `assets/diff-example.svg`.

### L4: mockup of the bot's reply (SVG, RU and EN)

**Owns:** `landing/assets/bot-reply-ru.svg`, `landing/assets/bot-reply-en.svg`, `how.mockup.*` (RU and EN).

**From the panel.** "I'll send two photos, and I want to know in advance what I'll get."

**Do:**
- A Telegram-style chat bubble holding one verdict in our format:
  - 2 problems, each with a fix;
  - 1 "send a photo that shows the back";
  - the count of matches.
- A visible "Макет" / "Mockup" label inside the picture.
- Text inside the SVG is fine, one file per language. Use `font-family="system-ui, sans-serif"`.

### L5: privacy in one place, plus a privacy page (copy, RU and EN)

**Owns:** `privacy.block`, `privacy.page_link`, `privacy_page.title`, `privacy_page.sections`.

**From the panel:**
- One seller sells unreleased designs.
- One designer works under NDA.
- The UK seller asked about GDPR and where the data lives.
- A marketer asked for a policy and a contact.

**Do:**
- `privacy.block`: one short paragraph with the facts above. It replaces the two scattered phrases.
- The page: 4–8 sections built strictly from the facts above:
  - what we store and why;
  - photos;
  - the model API;
  - where the server is;
  - deletion and contact;
  - changes to this policy.
- Add nothing we didn't list.

### L6: segment buttons that measure demand (copy, RU and EN)

**Owns:** `segments.h`, `segments.items`.

**From the panel:**
- A designer who makes cards for 12 sellers didn't see herself in "for sellers".
- A seller who hasn't moved to AI photos had no reason to press.

**Do:**
- 2–3 cards, each with its own `seg` tag, chosen from these:
  - `studio`: designers and studios working for several sellers;
  - `notyet`: sellers not on AI photos yet. Suggest generating one test photo anywhere and checking it here before switching the catalogue;
  - `other`: categories other than clothing and jewelry.
- Each button opens the bot with `/start landing_<lang>__<seg>`, so we count demand per segment.
- Promise only the waitlist.

### L7: English for the English market (copy)

**Owns:** `en.hero.kicker`, `why_telegram` (EN; RU may stay empty).

**From the panel.** A UK Etsy seller read "Wildberries, Ozon" as "a translated Russian product, not for me". She lives in email and Instagram, not Telegram.

**Do:**
- An EN kicker without Russian marketplaces.
- One line on why Telegram: no app or account to create, and the bot answers in the chat. Keep it honest; don't invent an email option.

### L8: social preview images (SVG 1200×630, RU and EN)

**Owns:** `landing/assets/og-ru.svg`, `landing/assets/og-en.svg`.

**From the panel.** Links to this page get pasted into Telegram chats and ads, and a preview without an image is plain text.

**Do:**
- The headline plus a tiny before/after with one circled difference.
- Readable as a 300 px thumbnail. Use fixed colours: previews don't know dark mode.
- `viewBox="0 0 1200 630"`.
- We render the PNG and set `meta.og_image`.

### L9: the bot's profile and replies (copy, RU and EN)

**Owns:** `app/bot_texts.json`: `name`, `short_description`, `description`, `welcome`, `fallback`.

**From the panel.** All six stopped at the bot's name. A marketer pointed out that between the page and `/start` there is a screen where the user sees only the bot's description and a Start button, and that is where people drop off.

**Do:**
- **`name`:** already decided and live: "LookTwin · Проверка фото" (RU) and "LookTwin · Photo check" (everyone else). Leave it as is.
- **`description`:** what people see before Start. Say what the bot does and that it's a waitlist now.
- **`short_description`:** the profile line.
- **`welcome`:** after `/start`, say thanks, say what happens next, and say the data facts in one line.
- **`fallback`:** the reply to any other message.
- We apply the profile fields through the Bot API.

### L10: reply in the visitor's language (code, contract tests)

**Owns:** `app/lang.py`, `pick_lang(start_text, language_code) -> "ru" | "en"`.

**Why.** Today someone from the EN page who presses `/start` gets a Russian reply.

**Contract:** `tests/contract/test_l10_lang.py`, 10 tests.

- The language tag in the `/start` payload wins, for example `landing_en__studio` gives `en`.
- Otherwise Telegram's `language_code` decides: `ru*` gives `ru`, anything else gives `en`.
- If there is nothing to go on, the answer is `ru`.
- It never raises.

Size: about 15 lines, standard library only. The wiring into `app/bot.py` is already done: it falls back to Russian until `IMPLEMENTED = True`.

### L12: name the case behind the numbers (copy, RU and EN)

**Owns:** `who.text`, `who.link_text`, `who.link_url`.

**From the panel and the board.** Three personas and one board reviewer didn't believe "11,246 photos for one jewelry brand" without a name or a link.

**Facts** (the owner allows naming the brand):
- the brand is **Каратов** (Karatov), a jewelry brand; the project ran in 2025–2026;
- 11,246 pieces of content in 4 months: 9,244 photos and 2,002 videos;
- 6 operators worked with the studio's Telegram bot in parallel;
- every piece had an automatic check that the jewelry in the image stayed itself;
- the public case: https://looktwinstudio.ru/cases/karatov/ (Russian).

**Do:**
- Rewrite `who.text` with the brand's name and the numbers. The bot puts the same kind of check in every seller's hands.
- Point the link to the case page; `link_text` says it's the case. On the EN page, say the case is in Russian.
- No claims beyond the facts: nothing about returns saved or accuracy.

### L11: audit of the assembled page (later)

Posted once L1–L8 land: accessibility, contrast in both themes, mobile layout, hreflang, how the text reads to a buyer. The result is a findings list and patches to the JSON.
