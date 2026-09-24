"""Builds the static landing pages (one per language) from one template.

python landing/build.py  ->  landing/ru/index.html, landing/en/index.html, landing/index.html
"""
import html
import pathlib

BOT = "contractsforpeoplebot"
ROOT = pathlib.Path(__file__).parent

T = {
    "ru": {
        "title": "Проверка ИИ-фото товара перед публикацией",
        "desc": "Бот сравнивает сгенерированное фото с настоящей вещью и показывает, что исказилось: принт, цвет, пуговицы, посадка, размер украшения.",
        "kicker": "Для продавцов на Wildberries, Ozon и других маркетплейсах",
        "h1": "ИИ-фото товара врёт вежливо. Бот покажет, где",
        "lead": "Пришлите фото из нейросети и фото настоящей вещи. Бот найдёт, что исказилось, и скажет, как это исправить, до того как покупатель оформит возврат.",
        "cta": "Проверить бесплатно в Telegram",
        "cta_note": "Первые проверки бесплатно. Фото удаляются сразу после проверки.",
        "problem_h": "Генератор не ломает картинку. Он подменяет товар",
        "problems": [
            ("Принт стал проще", "Сложный рисунок нейросеть перерисовывает по-своему. На фото красиво, в коробке другое."),
            ("Цвет «улучшился»", "Золото теплее, ткань ярче. Покупатель видит не тот оттенок, который получит."),
            ("Пропала деталь", "Пуговица, карман, камень в паве. Нейросеть не считает, она правдоподобно рисует."),
        ],
        "how_h": "Как это работает",
        "steps": [
            "Откройте бота и отправьте сгенерированное фото товара.",
            "Отправьте фото настоящей вещи: подойдёт снимок на телефон.",
            "Получите список расхождений и конкретное задание на исправление.",
        ],
        "checks_h": "Что проверяем",
        "checks": ["посадку и силуэт одежды", "ткань и фактуру", "принт и мелкий рисунок", "цвет относительно оригинала",
                   "число пуговиц, карманов, камней", "комплектность набора", "размер и пропорции украшений", "парные изделия"],
        "who_h": "Кто за этим стоит",
        "who": "LookTwinStudio делает рекламные фото и видео с ИИ для брендов одежды и украшений. Для одного ювелирного бренда наш конвейер выпустил 11 246 фото и видео за 4 месяца, с автоматической проверкой того, что изделие на кадре осталось собой. Бот делает такую проверку доступной каждому продавцу.",
        "who_link": "Сайт студии",
        "status": "Бот запускается. Нажмите /start, и мы напишем, как только проверка заработает для всех.",
        "privacy": "Мы храним только ваш Telegram ID и число проверок. Фото удаляются после обработки.",
        "lang_other": "English",
    },
    "en": {
        "title": "Check AI product photos before they go live",
        "desc": "A Telegram bot that compares a generated product photo with the real item and shows what changed: print, colour, buttons, fit, jewelry size.",
        "kicker": "For marketplace sellers on Amazon, Etsy, Wildberries, Ozon and beyond",
        "h1": "Your AI product photo lies politely. The bot shows where",
        "lead": "Send the generated photo and a photo of the real item. The bot finds what changed and tells you how to fix it, before a buyer files a return.",
        "cta": "Check for free in Telegram",
        "cta_note": "The first checks are free. Photos are deleted right after the check.",
        "problem_h": "A generator doesn't break the picture. It swaps the product",
        "problems": [
            ("The print got simpler", "Complex patterns get redrawn the model's way. Pretty on screen, different in the box."),
            ("The colour \"improved\"", "Warmer gold, brighter fabric. The buyer sees a shade they won't receive."),
            ("A detail went missing", "A button, a pocket, a stone in the pavé. Models don't count; they draw what looks plausible."),
        ],
        "how_h": "How it works",
        "steps": [
            "Open the bot and send the generated product photo.",
            "Send a photo of the real item; a phone snapshot is fine.",
            "Get a list of differences and a concrete fix.",
        ],
        "checks_h": "What we check",
        "checks": ["garment fit and silhouette", "fabric and texture", "prints and fine patterns", "colour against the original",
                   "count of buttons, pockets, stones", "whether a set is complete", "jewelry size and proportions", "identical pairs"],
        "who_h": "Who's behind it",
        "who": "LookTwinStudio makes AI advertising photos and video for clothing and jewelry brands. For one jewelry brand our pipeline produced 11,246 photos and videos in four months, with an automatic check that the piece in each image stayed itself. The bot puts that kind of check in every seller's hands.",
        "who_link": "Studio website",
        "status": "The bot is launching. Press /start and we'll message you as soon as checks open for everyone.",
        "privacy": "We store only your Telegram ID and a count of checks. Photos are deleted after processing.",
        "lang_other": "Русский",
    },
}

CSS = """
:root{--bg:#FBF8F3;--ink:#1B1B1F;--muted:#5B5B66;--card:#FFFFFF;--line:#E7E1D8;--accent:#D8402B;--accent-ink:#FFFFFF;--soft:#FFF1E8}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--ink:#F2F0EC;--muted:#A9A7B0;--card:#1E1E23;--line:#2E2E35;--accent:#FF6A4F;--accent-ink:#141417;--soft:#2A1F1B}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:center;padding:18px 0}
.brand{font-weight:700;letter-spacing:.2px}.lang{color:var(--muted);text-decoration:none;font-size:15px}
.hero{padding:36px 0 44px}.kicker{color:var(--accent);font-weight:600;font-size:15px;margin:0 0 12px}
h1{font-size:clamp(30px,6vw,52px);line-height:1.08;margin:0 0 18px;letter-spacing:-.5px}
.lead{font-size:clamp(17px,2.4vw,20px);color:var(--muted);max-width:640px;margin:0 0 28px}
.cta{display:inline-block;background:var(--accent);color:var(--accent-ink);text-decoration:none;font-weight:700;padding:16px 26px;border-radius:12px;font-size:18px}
.cta:focus-visible{outline:3px solid var(--ink);outline-offset:3px}
.note{color:var(--muted);font-size:14px;margin-top:12px}
section{padding:34px 0;border-top:1px solid var(--line)}h2{font-size:clamp(22px,3.6vw,30px);margin:0 0 20px;line-height:1.2}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}.card h3{margin:0 0 6px;font-size:18px}.card p{margin:0;color:var(--muted);font-size:15px}
ol.steps{padding-left:22px;margin:0}ol.steps li{margin:0 0 10px}
ul.checks{list-style:none;padding:0;margin:0;display:grid;gap:8px 18px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
ul.checks li{display:flex;gap:8px}ul.checks li:before{content:"✓";color:var(--accent);font-weight:700;flex:none}
.status{background:var(--soft);border-radius:14px;padding:18px;margin:0 0 18px}
footer{padding:28px 0 40px;color:var(--muted);font-size:14px;border-top:1px solid var(--line)}
a{color:inherit}
"""


def page(lang, other):
    t = T[lang]
    e = html.escape
    bot_link = f"https://t.me/{BOT}?start=landing_{lang}"
    problems = "".join(f'<div class="card"><h3>{e(h)}</h3><p>{e(p)}</p></div>' for h, p in t["problems"])
    steps = "".join(f"<li>{e(s)}</li>" for s in t["steps"])
    checks = "".join(f"<li>{e(c)}</li>" for c in t["checks"])
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(t["title"])}</title>
<meta name="description" content="{e(t["desc"])}">
<meta property="og:title" content="{e(t["title"])}">
<meta property="og:description" content="{e(t["desc"])}">
<meta property="og:type" content="website">
<link rel="alternate" hreflang="{other}" href="../{other}/">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header><span class="brand">LookTwinStudio · Photo check</span><a class="lang" href="../{other}/">{e(t["lang_other"])}</a></header>
<div class="hero">
<p class="kicker">{e(t["kicker"])}</p>
<h1>{e(t["h1"])}</h1>
<p class="lead">{e(t["lead"])}</p>
<a class="cta" href="{bot_link}" data-src="landing_{lang}">{e(t["cta"])}</a>
<p class="note">{e(t["cta_note"])}</p>
</div>
<section><h2>{e(t["problem_h"])}</h2><div class="grid">{problems}</div></section>
<section><h2>{e(t["how_h"])}</h2><ol class="steps">{steps}</ol></section>
<section><h2>{e(t["checks_h"])}</h2><ul class="checks">{checks}</ul></section>
<section><h2>{e(t["who_h"])}</h2><p>{e(t["who"])}</p><p><a href="https://looktwinstudio.ru/">{e(t["who_link"])}</a></p></section>
<section><p class="status">{e(t["status"])}</p><a class="cta" href="{bot_link}">{e(t["cta"])}</a></section>
<footer>{e(t["privacy"])}</footer>
</div>
<script>
// Pass an ad campaign tag through: ?src=tg_ads_1 turns into /start tg_ads_1
(function(){{var s=new URLSearchParams(location.search).get("src");if(!s||!/^[A-Za-z0-9_]{{1,40}}$/.test(s))return;
document.querySelectorAll("a.cta").forEach(function(a){{a.href="https://t.me/{BOT}?start="+s}});}})();
</script>
</body>
</html>
"""


for lang, other in (("ru", "en"), ("en", "ru")):
    out = ROOT / lang / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page(lang, other), encoding="utf-8")

(ROOT / "index.html").write_text("""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Photo check</title>
<script>var q=location.search;location.replace((/^ru\\b/i.test(navigator.language||"")?"ru/":"en/")+q);</script>
<noscript><meta http-equiv="refresh" content="0; url=ru/"></noscript></head>
<body><a href="ru/">Русский</a> · <a href="en/">English</a></body></html>
""", encoding="utf-8")
print("built:", [str(p.relative_to(ROOT)) for p in sorted(ROOT.rglob("index.html"))])
