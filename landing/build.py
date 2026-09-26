"""Builds the static landing pages from landing/content/{ru,en}.json and landing/assets/*.svg.

python landing/build.py  ->  landing/{ru,en}/index.html, landing/{ru,en}/privacy/index.html, landing/index.html

Texts live in content/*.json and pictures in assets/, so each can be changed
without touching this template. An empty value means "don't render this block".
Rules for both are checked by tests/test_landing_content.py.
"""
import html
import json
import pathlib

BOT = "contractsforpeoplebot"
SITE = "https://itsalt.github.io/photo-check-bot"
ROOT = pathlib.Path(__file__).parent
LANGS = ("ru", "en")

CSS = """
:root{--bg:#FBF8F3;--ink:#1B1B1F;--muted:#5B5B66;--card:#FFFFFF;--line:#E7E1D8;--accent:#D8402B;--accent-ink:#FFFFFF;--soft:#FFF1E8}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--ink:#F2F0EC;--muted:#A9A7B0;--card:#1E1E23;--line:#2E2E35;--accent:#FF6A4F;--accent-ink:#141417;--soft:#2A1F1B}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:center;padding:18px 0}
.brand{font-weight:700;letter-spacing:.2px;color:inherit;text-decoration:none}.lang{color:var(--muted);text-decoration:none;font-size:15px}
.hero{padding:36px 0 44px}.kicker{color:var(--accent);font-weight:600;font-size:15px;margin:0 0 12px}
h1{font-size:clamp(30px,6vw,52px);line-height:1.08;margin:0 0 18px;letter-spacing:-.5px}
.lead{font-size:clamp(17px,2.4vw,20px);color:var(--muted);max-width:640px;margin:0 0 28px}
.cta{display:inline-block;background:var(--accent);color:var(--accent-ink);text-decoration:none;font-weight:700;padding:16px 26px;border-radius:12px;font-size:18px}
.cta.secondary{background:transparent;color:var(--accent);border:2px solid var(--accent);padding:10px 18px;font-size:16px}
.cta:focus-visible{outline:3px solid var(--ink);outline-offset:3px}
.note{color:var(--muted);font-size:14px;margin:12px 0 0}
section{padding:34px 0;border-top:1px solid var(--line)}h2{font-size:clamp(22px,3.6vw,30px);margin:0 0 20px;line-height:1.2}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}.card h3{margin:0 0 6px;font-size:18px}.card p{margin:0 0 12px;color:var(--muted);font-size:15px}.card p:last-child{margin:0}
ol.steps{padding-left:22px;margin:0}ol.steps li{margin:0 0 10px}
h3.group{font-size:17px;margin:18px 0 10px}h3.group:first-child{margin-top:0}
ul.checks{list-style:none;padding:0;margin:0;display:grid;gap:8px 18px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
ul.checks li{display:flex;gap:8px}ul.checks li:before{content:"✓";color:var(--accent);font-weight:700;flex:none}
figure{margin:24px 0 0}figure .pic svg{display:block;width:100%;height:auto}figcaption{color:var(--muted);font-size:14px;margin-top:8px}
.status{background:var(--soft);border-radius:14px;padding:18px;margin:0 0 18px}
footer{padding:28px 0 40px;color:var(--muted);font-size:14px;border-top:1px solid var(--line)}footer p{margin:0 0 8px}
.doc h1{font-size:clamp(26px,5vw,40px)}.doc h2{font-size:21px;margin:28px 0 8px}.doc p{margin:0 0 12px}
a{color:inherit}
"""

SCRIPT = """<script>
// Pass an ad campaign tag through: ?src=tg_ads_1 turns into /start tg_ads_1 (segment buttons add __<seg>)
(function(){var s=new URLSearchParams(location.search).get("src");if(!s||!/^[A-Za-z0-9_]{1,30}$/.test(s))return;
document.querySelectorAll("a.cta").forEach(function(a){var g=a.getAttribute("data-seg");a.href="https://t.me/%s?start="+s+(g?"__"+g:"")});})();
</script>""" % BOT


def load(lang):
    return json.loads((ROOT / "content" / f"{lang}.json").read_text(encoding="utf-8"))


def asset(name):
    """Inline an SVG from assets/ (safety rules are enforced by the tests)."""
    svg = (ROOT / name).read_text(encoding="utf-8").strip()
    if svg.startswith("<?xml"):
        svg = svg[svg.index("?>") + 2:].strip()
    return svg


def figure(block):
    if not block.get("asset"):
        return ""
    e = html.escape
    cap = f"<figcaption>{e(block['caption'])}</figcaption>" if block.get("caption") else ""
    return f'<figure><div class="pic" role="img" aria-label="{e(block["alt"])}">{asset(block["asset"])}</div>{cap}</figure>'


def head(c, lang, other, path, title):
    e = html.escape
    m = c["meta"]
    og = ""
    if m.get("og_image"):
        og = (f'<meta property="og:image" content="{SITE}/{m["og_image"]}">\n'
              '<meta property="og:image:width" content="1200">\n<meta property="og:image:height" content="630">\n'
              '<meta name="twitter:card" content="summary_large_image">\n')
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(m["desc"])}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(m["desc"])}">
<meta property="og:type" content="website">
<meta property="og:url" content="{SITE}/{lang}/{path}">
{og}<link rel="alternate" hreflang="{lang}" href="{SITE}/{lang}/{path}">
<link rel="alternate" hreflang="{other}" href="{SITE}/{other}/{path}">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header><a class="brand" href="{SITE}/{lang}/">{e(c["header"]["brand"])}</a><a class="lang" href="../{"../" if path else ""}{other}/{path}">{e(c["header"]["lang_other"])}</a></header>
"""


def footer(c, lang, on_privacy_page=False):
    e = html.escape
    p = c["privacy"]
    link = ""
    if p.get("page_link") and c["privacy_page"]["sections"] and not on_privacy_page:
        link = f'<p><a href="{SITE}/{lang}/privacy/">{e(p["page_link"])}</a></p>'
    return f"<footer><p>{e(p['block'])}</p>{link}</footer>\n</div>\n"


def page(lang, other):
    c = load(lang)
    e = html.escape
    h = c["hero"]
    start = f"landing_{lang}"

    def cta(text, cls="cta", seg=""):
        payload = f"{start}__{seg}" if seg else start
        data = f' data-seg="{e(seg)}"' if seg else ""
        return f'<a class="{cls}" href="https://t.me/{BOT}?start={payload}"{data}>{e(text)}</a>'

    price = f'<p class="note">{e(h["price_note"])}</p>' if h.get("price_note") else ""
    problems = "".join(f'<div class="card"><h3>{e(i["title"])}</h3><p>{e(i["text"])}</p></div>' for i in c["problems"]["items"])
    steps = "".join(f"<li>{e(s)}</li>" for s in c["how"]["steps"])
    groups = ""
    for g in c["checks"]["groups"]:
        title = f'<h3 class="group">{e(g["title"])}</h3>' if g.get("title") else ""
        groups += title + '<ul class="checks">' + "".join(f"<li>{e(i)}</li>" for i in g["items"]) + "</ul>"
    checks_note = f'<p class="note">{e(c["checks"]["note"])}</p>' if c["checks"].get("note") else ""
    segments = ""
    if c["segments"]["items"]:
        cards = "".join(
            f'<div class="card"><h3>{e(s["title"])}</h3><p>{e(s["text"])}</p><p>{cta(s["cta"], "cta secondary", s["seg"])}</p></div>'
            for s in c["segments"]["items"])
        segments = f'<section><h2>{e(c["segments"]["h"])}</h2><div class="grid">{cards}</div></section>'
    why = f'<p class="note">{e(c["why_telegram"])}</p>' if c.get("why_telegram") else ""
    w = c["who"]
    return head(c, lang, other, "", c["meta"]["title"]) + f"""<div class="hero">
<p class="kicker">{e(h["kicker"])}</p>
<h1>{e(h["h1"])}</h1>
<p class="lead">{e(h["lead"])}</p>
{cta(h["cta"])}
<p class="note">{e(h["cta_note"])}</p>{price}{why}
{figure(c["illustration"])}
</div>
<section><h2>{e(c["problems"]["h"])}</h2><div class="grid">{problems}</div></section>
<section><h2>{e(c["how"]["h"])}</h2><ol class="steps">{steps}</ol>{figure(c["how"]["mockup"])}</section>
<section><h2>{e(c["checks"]["h"])}</h2>{groups}{checks_note}</section>
{segments}
<section><h2>{e(w["h"])}</h2><p>{e(w["text"])}</p><p><a href="{e(w["link_url"])}">{e(w["link_text"])}</a></p></section>
<section><p class="status">{e(c["status"]["text"])}</p>{cta(c["status"]["cta"])}</section>
""" + footer(c, lang) + SCRIPT + "\n</body>\n</html>\n"


def privacy_page(lang, other):
    c = load(lang)
    e = html.escape
    pp = c["privacy_page"]
    body = "".join(f"<h2>{e(s['h'])}</h2><p>{e(s['p'])}</p>" for s in pp["sections"])
    return (head(c, lang, other, "privacy/", pp["title"])
            + f'<div class="doc"><h1>{e(pp["title"])}</h1>{body}</div>\n'
            + footer(c, lang, on_privacy_page=True) + "</body>\n</html>\n")


def build():
    built = []
    for lang, other in (("ru", "en"), ("en", "ru")):
        out = ROOT / lang / "index.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(page(lang, other), encoding="utf-8", newline="\n")
        built.append(out)
        if load(lang)["privacy_page"]["sections"]:
            out = ROOT / lang / "privacy" / "index.html"
            out.parent.mkdir(exist_ok=True)
            out.write_text(privacy_page(lang, other), encoding="utf-8", newline="\n")
            built.append(out)
    (ROOT / "index.html").write_text("""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Photo check</title>
<script>var q=location.search;location.replace((/^ru\\b/i.test(navigator.language||"")?"ru/":"en/")+q);</script>
<noscript><meta http-equiv="refresh" content="0; url=ru/"></noscript></head>
<body><a href="ru/">Русский</a> · <a href="en/">English</a></body></html>
""", encoding="utf-8", newline="\n")
    return built


if __name__ == "__main__":
    print("built:", [str(p.relative_to(ROOT)) for p in build()])
