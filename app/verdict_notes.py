"""Seller-facing notes under the verdict, from the vision_core.assess result.

Russian by default, English with lang="en" (the labels stay as the model wrote them,
in Russian). Plain text. Labels were sanitised in vision_core.label_of. A malformed or
empty result gives "" and the plain verdict stands.

Out-of-view details (WP-CONTROLS-04, D-10): `not_compared` items are never listed under
"Проверьте сами"; they get one neutral line, the only note a MATCH can carry. The
`too_few_compared` reason (the MATCH minimum of rule A' failed) gets its own short line.
"""
MAX_ITEMS = 6
REASON_RU = {
    "not_visible": "не видно",
    "orders_disagree": "ответы расходятся",
    "weak_evidence": "не уверены",
    "fine_detail_unverified": "мелкая деталь",
}
REASON_EN = {
    "not_visible": "not visible",
    "orders_disagree": "answers disagree",
    "weak_evidence": "not sure",
    "fine_detail_unverified": "fine detail",
}
WORDS = {
    "ru": {"reasons": REASON_RU, "more": " и ещё %d", "matches": "Совпадает: %d из %d.",
           "differs": "Отличается: %s.", "to_check": "Проверьте сами: %s.",
           "not_compared": "Не сравнивалось (на ИИ-фото это место не видно): %s.",
           "too_few_compared": "Сравнить удалось слишком мало деталей, поэтому посмотрите фото сами.",
           "extra_confirmed": "На сгенерированном фото есть лишняя деталь товара.",
           "extra_unconfirmed": "Возможна лишняя деталь товара на сгенерированном фото."},
    "en": {"reasons": REASON_EN, "more": " and %d more", "matches": "Matches: %d of %d.",
           "differs": "Differs: %s.", "to_check": "Check yourself: %s.",
           "not_compared": "Not compared (this place is not visible on the AI photo): %s.",
           "too_few_compared": "Too few details could be compared, so please look at the photos yourself.",
           "extra_confirmed": "The generated photo has an extra product detail.",
           "extra_unconfirmed": "The generated photo may have an extra product detail."},
}


def _items(entries, with_reason, words=WORDS["ru"]):
    out = []
    for e in entries[:MAX_ITEMS]:
        out.append("%s (%s)" % (e["label"], words["reasons"][e["reason"]]) if with_reason else e["label"])
    rest = len(entries) - MAX_ITEMS
    return ", ".join(out) + (words["more"] % rest if rest > 0 else "")


def format_notes(result, lang="ru"):
    words = WORDS.get(lang, WORDS["ru"])
    try:
        status, total = result["status"], result["total"]
        confirmed, to_check = result["confirmed"], result["to_check"]
        differs, extra = result["differs"], result["extra"]
        if status not in ("MATCH", "MISMATCH", "NEEDS_REVIEW") or not isinstance(total, int):
            return ""
        # absent in results built before WP-CONTROLS-04: nothing was left out then
        not_compared = result.get("not_compared") or []
        hidden = {e["attribute_id"] for e in not_compared}
        skipped = words["not_compared"] % _items(not_compared, False, words) if not_compared else ""
        if status == "MATCH":
            return skipped
        parts = [words["matches"] % (len(confirmed), total)]
        if differs:
            parts.append(words["differs"] % _items(differs, False, words))
        # an out-of-view detail is never something to check, whatever the caller put in to_check
        to_check = [e for e in to_check if e.get("attribute_id") not in hidden]
        if to_check:
            parts.append(words["to_check"] % _items(to_check, True, words))
        if skipped:
            parts.append(skipped)
        if extra == "confirmed":
            parts.append(words["extra_confirmed"])
        elif extra == "unconfirmed":
            parts.append(words["extra_unconfirmed"])
        if result.get("reason") == "too_few_compared":
            parts.append(words["too_few_compared"])
        return " ".join(parts)
    except (KeyError, TypeError, AttributeError):
        return ""
