IMPLEMENTED = True
# T1b: per-user state machine that pairs the "generated" and "real" photos.
# Standard library only and pure: no I/O, and neither input is ever mutated.
#
# step(state, event, now) -> (new_state, actions)
#   state   None, or {"stage", "generated", "real", "updated"}; stage is
#           "idle", "have_generated" or "have_real".
#   actions {"say": key} for a message to the user, and
#           {"do": "check", "generated": id, "real": id} once a pair is complete.
#
# Default order: generated, then real. A caption can label a photo. Latin
# markers match whole words ("detail" is not "ai"), Russian markers match
# stems ("сгенерированное" is "ген"). A caption with both labels counts as
# unlabelled. A photo that fills the missing half completes the pair; one with
# the same label as the half already held replaces it. A half pair older than
# 30 minutes (strictly more than EXPIRY_S) expires before the event is handled.
import re

EXPIRY_S = 30 * 60
IMAGE_KINDS = ("photo", "image_doc")
ASK = {"idle": "ask_generated", "have_generated": "ask_real", "have_real": "ask_generated"}

_REAL_LAT = re.compile(r"\breal\b")
_GEN_LAT = re.compile(r"\bai\b")
_REAL_RU = ("оригинал", "настоящ")
_GEN_RU = ("нейро", "ген")


def _label(caption):
    text = str(caption or "").lower()
    real = bool(_REAL_LAT.search(text)) or any(s in text for s in _REAL_RU)
    gen = bool(_GEN_LAT.search(text)) or any(s in text for s in _GEN_RU)
    if real == gen:
        return None
    return "real" if real else "generated"


def _state(stage, generated, real, updated):
    return {"stage": stage, "generated": generated, "real": real, "updated": updated}


def step(state, event, now):
    s = state if isinstance(state, dict) else {}
    stage, gen, real, updated = s.get("stage"), s.get("generated"), s.get("real"), s.get("updated")
    # A half pair without its half, or with an unknown stage, is treated as empty.
    if not ((stage == "have_generated" and gen) or (stage == "have_real" and real)):
        stage, gen, real = "idle", None, None
    actions = []
    if stage != "idle" and (not isinstance(updated, (int, float)) or now - updated > EXPIRY_S):
        actions.append({"say": "expired"})
        stage, gen, real = "idle", None, None
    if stage == "idle":
        gen, real, updated = None, None, now

    ev = event if isinstance(event, dict) else {}
    kind = ev.get("kind")
    if kind == "command" and ev.get("command") in ("start", "cancel"):
        say = "ask_generated" if ev["command"] == "start" else "cancelled"
        return _state("idle", None, None, now), actions + [{"say": say}]
    if kind in IMAGE_KINDS and ev.get("file_id"):
        fid, label = ev["file_id"], _label(ev.get("caption"))
        if stage == "have_generated" and label != "generated":
            return _state("idle", None, None, now), actions + [{"do": "check", "generated": gen, "real": fid}]
        if stage == "have_real" and label != "real":
            return _state("idle", None, None, now), actions + [{"do": "check", "generated": fid, "real": real}]
        if label == "real":
            return _state("have_real", None, fid, now), actions + [{"say": "ask_generated"}]
        return _state("have_generated", fid, None, now), actions + [{"say": "ask_real"}]
    # Anything else keeps the pair as it is, including its age.
    say = "unsupported" if kind in IMAGE_KINDS + ("unsupported",) else ASK[stage]
    return _state(stage, gen, real, updated), actions + [{"say": say}]
