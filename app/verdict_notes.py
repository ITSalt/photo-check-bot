"""Seller-facing notes under the verdict, from the vision_core.assess result.

Russian only (like photo_checks.MESSAGES). Plain text. Labels were sanitised in
vision_core.label_of. A malformed or empty result gives "" and the plain verdict stands.
"""
MAX_ITEMS = 6
REASON_RU = {
    "not_visible": "не видно",
    "orders_disagree": "ответы расходятся",
    "weak_evidence": "не уверены",
    "fine_detail_unverified": "мелкая деталь",
}


def _items(entries, with_reason):
    out = []
    for e in entries[:MAX_ITEMS]:
        out.append("%s (%s)" % (e["label"], REASON_RU[e["reason"]]) if with_reason else e["label"])
    rest = len(entries) - MAX_ITEMS
    return ", ".join(out) + (" и ещё %d" % rest if rest > 0 else "")


def format_notes(result):
    try:
        status, total = result["status"], result["total"]
        confirmed, to_check = result["confirmed"], result["to_check"]
        differs, extra = result["differs"], result["extra"]
        if status not in ("MATCH", "MISMATCH", "NEEDS_REVIEW") or not isinstance(total, int):
            return ""
        if status == "MATCH":
            return ""
        parts = ["Совпадает: %d из %d." % (len(confirmed), total)]
        if differs:
            parts.append("Отличается: %s." % _items(differs, False))
        if to_check:
            parts.append("Проверьте сами: %s." % _items(to_check, True))
        if extra == "confirmed":
            parts.append("На сгенерированном фото есть лишняя деталь товара.")
        elif extra == "unconfirmed":
            parts.append("Возможна лишняя деталь товара на сгенерированном фото.")
        return " ".join(parts)
    except (KeyError, TypeError, AttributeError):
        return ""
