"""T1f: the message a seller sees after a check. Standard library only.

format_verdict(results, lang="ru", stub=False) -> str
Order: problems (MISMATCH with its fix, then ASYMMETRIC_PASS as "not confirmed"), then details
the reference photo does not show ("send a photo that shows ..."), then a count of matches.
ASYMMETRIC_PASS is never counted or worded as a match. No percentages. Unknown lang -> English.
Never longer than the 4096-character message limit: overflow is cut at a whole line and counted.
"""
IMPLEMENTED = True

LIMIT = 4096
CLIP = 300  # one reason or fix never takes more than this

T = {
    "ru": {
        "demo": "ДЕМО — это сценарий интерфейса, а не настоящая проверка.",
        "problems": "Перед публикацией поправьте:",
        "mismatch": "✗ {d}: {r}.",
        "fix": "  Что сделать: {f}.",
        "asym": "? {d}: не подтверждено — сходство видно только при сравнении в одну сторону. Проверьте глазами.",
        "missing": "Пришлите фото, где видно: {ds}. Без него это не проверить.",
        "matches": "Сошлось деталей: {n} ({ds}).",
        "all": "Всё проверенное сошлось: {n} ({ds}).",
        "empty": "Проверять пока нечего: пришлите сгенерированное фото и фото настоящей вещи.",
        "more": "…и ещё пунктов: {n}. Поправьте первые и проверьте снова.",
    },
    "en": {
        "demo": "DEMO — this shows the interface, not a real check.",
        "problems": "Fix before publishing:",
        "mismatch": "✗ {d}: {r}.",
        "fix": "  What to do: {f}.",
        "asym": "? {d}: not confirmed — it only looks alike when compared one way. Check it by eye.",
        "missing": "Send a photo that shows: {ds}. It can't be checked without one.",
        "matches": "Details that agree: {n} ({ds}).",
        "all": "Everything checked agrees: {n} ({ds}).",
        "empty": "Nothing to check yet: send the generated photo and a photo of the real item.",
        "more": "…and {n} more. Fix the first ones and check again.",
    },
}


def _clip(s):
    s = " ".join(str(s or "").split()).rstrip(".")
    return s if len(s) <= CLIP else s[:CLIP - 1].rstrip() + "…"


def format_verdict(results, lang="ru", stub=False):
    t = T.get(lang, T["en"])
    by = {"MISMATCH": [], "ASYMMETRIC_PASS": [], "NOT_VISIBLE_IN_REFERENCE": [], "MATCH": []}
    for r in results or []:
        by.get(r.get("verdict"), by["MISMATCH"]).append(r)   # an unknown verdict is shown as a problem, never as a match
    head = [t["demo"]] if stub else []
    body = []
    if by["MISMATCH"] or by["ASYMMETRIC_PASS"]:
        body.append(t["problems"])
        for r in by["MISMATCH"]:
            item = [t["mismatch"].format(d=_clip(r.get("detail")), r=_clip(r.get("reason")))]
            if r.get("fix"):
                item.append(t["fix"].format(f=_clip(r["fix"])))
            body.append("\n".join(item))
        for r in by["ASYMMETRIC_PASS"]:
            body.append(t["asym"].format(d=_clip(r.get("detail"))))
    if by["NOT_VISIBLE_IN_REFERENCE"]:
        body.append(t["missing"].format(ds=", ".join(_clip(r.get("detail")) for r in by["NOT_VISIBLE_IN_REFERENCE"])))
    if by["MATCH"]:
        key = "matches" if body else "all"
        body.append(t[key].format(n=len(by["MATCH"]), ds=", ".join(_clip(r.get("detail")) for r in by["MATCH"])))
    if not body:
        body.append(t["empty"])
    out, used = list(head), sum(len(x) + 1 for x in head)
    for i, part in enumerate(body):
        tail = t["more"].format(n=len(body) - i)
        if used + len(part) + 1 + len(tail) + 1 > LIMIT and i < len(body) - 1 or used + len(part) + 1 > LIMIT:
            out.append(tail)
            break
        out.append(part)
        used += len(part) + 1
    return "\n".join(out)[:LIMIT]
