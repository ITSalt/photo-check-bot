# S1 ledger by TheOne. Contract: f7631721; clarifications: fc9064fb, 52fc6c93.
import copy


def new_state():
    return {"day": None, "spent": 0, "holds": {}, "memory": {}}


def _trim(s, cfg):
    order = sorted((v["at"], hid) for hid, v in s["memory"].items())
    for _, hid in order[:max(0, len(order) - cfg["memory_max"])]:
        del s["memory"][hid]


def _remember(s, hid, kind, t, cfg, usd=0):
    s["memory"][hid] = {"kind": kind, "at": t, "usd": usd}
    _trim(s, cfg)


def _maintain(s, t, cfg):
    day = t // 86400
    if s["day"] != day:
        s["day"], s["spent"] = day, 0
    for hid, h in list(s["holds"].items()):
        if (h["orphan"] and h["at"] // 86400 < day
                and t - h["at"] > cfg["lifetime"]):
            del s["holds"][hid]
            _remember(s, hid, "expired", t, cfg)
    for hid, remembered in list(s["memory"].items()):
        if t - remembered["at"] >= cfg["memory"]:
            del s["memory"][hid]
    _trim(s, cfg)
    return s


def _total(s):
    return s["spent"] + sum(h["left"] for h in s["holds"].values())


def counted(state, t, cfg):
    # Pure read: lazy maintenance is evaluated on an uncommitted copy.
    return _total(_maintain(copy.deepcopy(state), t, cfg))


def apply(state, event, cfg):
    t, op = event["t"], event["op"]
    s = _maintain(copy.deepcopy(state), t, cfg)
    hid, usd = event.get("id"), event.get("usd", 0)
    h, remembered = s["holds"].get(hid), s["memory"].get(hid)
    booked = 0
    if op == "reserve":
        result = "refused"
        if h is None and remembered is None and _total(s) + usd <= cfg["cap"]:
            s["holds"][hid] = {"at": t, "left": usd, "orphan": False}
            result = "held"
    elif op == "record":
        result, booked = "recorded", usd
        if h is not None:
            h["left"] = max(0, h["left"] - usd)
    elif op == "close":
        result = "noop"
        if h is not None and not h["orphan"]:
            del s["holds"][hid]
            _remember(s, hid, "released", t, cfg)
            result = "released"
    elif op == "crash":
        for hold in s["holds"].values():
            hold["orphan"] = True
        result = "crashed"
    elif op == "settle":
        if h is not None and not h["orphan"]:
            result = "live"
        elif remembered is not None and remembered["kind"] == "released":
            result = "released"
        else:
            high = usd
            if h is not None:
                del s["holds"][hid]
                result, booked = "settled", usd
            elif remembered is None:
                result, booked = "unknown", usd
            elif remembered["kind"] == "expired":
                result, booked = "expired", usd
            else:
                result = "repeat"
                high = max(usd, remembered["usd"])
                booked = high - remembered["usd"]
            _remember(s, hid, "settled", t, cfg, high)
    else:
        raise ValueError("unknown operation")
    s["spent"] += booked
    return s, {"result": result, "booked": booked}
