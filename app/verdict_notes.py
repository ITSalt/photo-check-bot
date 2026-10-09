"""Seller-facing notes under the verdict, from the vision_core.assess result.

Russian by default, English with lang="en" (the labels stay as the model wrote them,
in Russian). Plain text. Labels were sanitised in vision_core.label_of. A malformed or
empty result gives "" and the plain verdict stands.
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
           "extra_confirmed": "На сгенерированном фото есть лишняя деталь товара.",
           "extra_unconfirmed": "Возможна лишняя деталь товара на сгенерированном фото."},
    "en": {"reasons": REASON_EN, "more": " and %d more", "matches": "Matches: %d of %d.",
           "differs": "Differs: %s.", "to_check": "Check yourself: %s.",
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
        if status == "MATCH":
            return ""
        parts = [words["matches"] % (len(confirmed), total)]
        if differs:
            parts.append(words["differs"] % _items(differs, False, words))
        if to_check:
            parts.append(words["to_check"] % _items(to_check, True, words))
        if extra == "confirmed":
            parts.append(words["extra_confirmed"])
        elif extra == "unconfirmed":
            parts.append(words["extra_unconfirmed"])
        return " ".join(parts)
    except (KeyError, TypeError, AttributeError):
        return ""
