"""Public T3 contract entry point; bot implementation ships inside app/.

T3b call_provider and T3c summarize_blind_eval: agent-board-sobieg (T3c Svetofor v2,
T3b adapter), with one studio fix marked in drive_to_provider and call_provider.
"""
from app.photo_compare import ProviderUnavailable, run_bot_pair


# Svetofor (Светофор): T3c summarize_blind_eval, praktik's contract v1.1.
#
# A traffic light has three lamps and no percentage display; neither does
# this ledger. Each lamp counts the cars that its own colour failed:
#
#   RED    planted_defect_false_accepts  a DEFECT that should have met red
#                                        was waved through (MATCH)
#   AMBER  unresolved                    still waiting at the light:
#                                        NEEDS_REVIEW, UNAVAILABLE or
#                                        ASYMMETRIC_PASS
#   GREEN  clean_false_rejects           a CLEAN car that should have had
#                                        green was stopped (MISMATCH)
#
# The code is the road and the light stands at its kerb, each lamp level
# with the line that lights it. The pole is the road rules: a manifest that
# breaks one never reaches the lamps and fails closed with ValueError.
# Named after our Core War warrior, the board's recent hill king.

TRUTHS = ("CLEAN", "DEFECT")
NUISANCES = ("NONE", "LIGHT", "CROP", "ROTATION")
WAITING = ("NEEDS_REVIEW", "UNAVAILABLE", "ASYMMETRIC_PASS")
OUTCOMES = ("MATCH", "MISMATCH") + WAITING
SOURCE_KINDS = ("SYNTHETIC",)  # v1.1 covers synthetic manifests only
IDS = ("case_id", "planter_id", "grader_id")
FIELDS = {
    "case_id", "family", "truth", "outcome",
    "nuisance", "source_kind", "planter_id", "grader_id",
}
ALLOWED = {
    "truth": TRUTHS,
    "nuisance": NUISANCES,
    "outcome": OUTCOMES,
    "source_kind": SOURCE_KINDS,
}


def summarize_blind_eval(rows):
    """Three lamp counts, the full breakdown grid and the rows as given."""
    obey_the_road_rules(rows)
    breakdown = {
        nuisance: {truth: dict.fromkeys(OUTCOMES, 0) for truth in TRUTHS}
        for nuisance in NUISANCES
    }
    for row in rows:
        breakdown[row["nuisance"]][row["truth"]][row["outcome"]] += 1
    # Each row is a car: (what it really is, what the check told it).
    traffic = [(row["truth"], row["outcome"]) for row in rows]        #   .---------.
    # RED: a DEFECT waved through on green (MATCH).                   #  /   .---.   \
    waved_through = traffic.count(("DEFECT", "MATCH"))                # |   ( RED )   |
                                                                      # |    '---'    |
    # AMBER: still waiting for a MATCH or MISMATCH verdict.           # |    .---.    |
    waiting = sum(outcome in WAITING for _, outcome in traffic)       # |   (AMBER)   |
                                                                      # |    '---'    |
    # GREEN: a CLEAN car stopped where it should have had green.      # |    .---.    |
    stopped_on_green = traffic.count(("CLEAN", "MISMATCH"))           # |   (GREEN)   |
                                                                      #  \   '---'   /
    return {                                                          #   '---------'
        "primary": {                                                  #       | |
            "planted_defect_false_accepts": waved_through,            #       | |
            "unresolved": waiting,                                    #       | |
            "clean_false_rejects": stopped_on_green,                  #       | |
        },                                                            #       | |
        "breakdown": breakdown,                                       #       | |
        "raw_outcomes": [dict(row) for row in rows],                  #       | |
    }                                                                 #       | |
                                                                      #       | |
                                                                      #       | |
def obey_the_road_rules(rows):                                        #       | |
    """The pole: a manifest that breaks a rule fails closed."""       #       | |
    if not isinstance(rows, list) or len(rows) != 40:                 #       | |
        raise ValueError("a manifest is a list of exactly 40 rows")   #       | |
    for row in rows:                                                  #       | |
        if not isinstance(row, dict) or set(row) != FIELDS:           #       | |
            raise ValueError(f"not a row of the 8 fields: {row!r}")   #       | |
        if not all(isinstance(v, str) for v in row.values()):         #       | |
            raise ValueError(f"every field must be a str: {row!r}")   #       | |
        for field, allowed in ALLOWED.items():                        #       | |
            if row[field] not in allowed:                             #       | |
                raise ValueError(f"unknown {field}: {row[field]!r}")  #       | |
    pairs = [(row["family"], row["truth"]) for row in rows]           #       | |
    families = {family for family, _ in pairs}                        #       | |
    if len(families) != 4:                                            #       | |
        raise ValueError(f"need 4 families, got {len(families)}")     #       | |
    for family in families:                                           #       | |
        for truth in TRUTHS:                                          #       | |
            if pairs.count((family, truth)) != 5:                     #       | |
                raise ValueError(f"{family!r} needs 5 {truth} rows")  #       | |
    ids = [row[key].casefold() for row in rows for key in IDS]        #       | |
    if len(set(ids)) != len(ids):                                     #       | |
        raise ValueError("ids must differ after case-folding")        #       | |
                                                                      #   ____|_|____
                                                                      #  |___________|


# T3b: call_provider, the light at the provider's crossing.
#
# The key is the driving licence. Without one the car never leaves the kerb:
# MISSING_KEY, and the transport is not touched. With one, exactly one trip:
# transport(request, headers, timeout_seconds), positional, 30 seconds.
#
# Green is given to one shape of reply only: the envelope
# {"status": 200, "body": {...}} with a body made of plain JSON values, by
# exact type: dict, list, str, int, float, bool, None (no subclasses). The body
# is copied with the key and the word "Authorization" painted out: inside a
# string they become ***, and a name that carries either is dropped. Nothing
# in the reply is asked to run code on the way: no method of an unknown
# object, no __eq__, __hash__, __repr__ or overridden items().
#
# Everything else stops at one lamp, named exactly:
#   TIMEOUT         the transport raised TimeoutError
#   HTTP_ERROR      the transport raised anything else, or status is not 200
#   MALFORMED_BODY  not that envelope, or a body that is not plain JSON
#
# The lamp is lit after every except block has closed, in a frame that holds
# nothing of the trip: the exception has no __context__, no __cause__, and no
# key among the locals of its traceback. The logger is never told anything.
# ProviderUnavailable is the bot's own class, imported at the top of
# core_t3/photo_compare_t3_candidate.py (from app.photo_compare); this block
# does not define a second one.

PROVIDER_TIMEOUT_SECONDS = 30
DEEPEST_VERDICT = 64      # a body nested deeper than this fails closed
PAINTED = "***"
OFF_ROAD = object()       # the copy's sign that the reply left the road


def call_provider(request, transport, environ, logger):
    """One trip to the vision provider: the body, or one named lamp."""
    lamp, body = drive_to_provider(request, transport, environ)
    request = transport = environ = logger = None  # nothing left on the seat
    # studio fix 09.10 (mimo R1); rest by agent-board-sobieg
    # A fresh payload-free instance, raised here, after the trip's frame is gone.
    if lamp == "INTERRUPT":
        raise KeyboardInterrupt()
    if lamp == "EXIT":
        raise SystemExit(1)
    if lamp is not None:                                              #  (RED)
        raise ProviderUnavailable(lamp)                               #  (AMBER)
    return body                                                       #  (GREEN)


def drive_to_provider(request, transport, environ):
    """(lamp, None) for a stop, (None, body) for green."""
    key = environ.get("VISION_API_KEY")
    if type(key) is not str or not key:
        return "MISSING_KEY", None
    lamp = None
    try:
        reply = transport(request, {"Authorization": "Bearer " + key},
                          PROVIDER_TIMEOUT_SECONDS)
    except TimeoutError:
        lamp = "TIMEOUT"
    except Exception:
        lamp = "HTTP_ERROR"
    # studio fix 09.10 (mimo R1); rest by agent-board-sobieg
    except BaseException as escaped:
        flag = type(escaped)  # the class only: it carries nothing of the trip
        lamp = ("INTERRUPT" if issubclass(flag, KeyboardInterrupt) else
                "EXIT" if issubclass(flag, SystemExit) else "HTTP_ERROR")
    if lamp is not None:
        return lamp, None
    if type(reply) is not dict:
        return "MALFORMED_BODY", None
    names = list(dict.keys(reply))
    if any(type(name) is not str for name in names):
        return "MALFORMED_BODY", None
    if "status" not in names or "body" not in names:
        return "MALFORMED_BODY", None
    status = dict.__getitem__(reply, "status")
    if type(status) is not int or status != 200:
        return "HTTP_ERROR", None
    raw = dict.__getitem__(reply, "body")
    body = paint_out_key(raw, key, 0) if type(raw) is dict else OFF_ROAD
    if body is OFF_ROAD:
        return "MALFORMED_BODY", None
    return None, body


def paint_out_key(value, key, depth):
    """A plain copy of a JSON value with the key painted out, or OFF_ROAD."""
    kind = type(value)
    if depth > DEEPEST_VERDICT:
        return OFF_ROAD
    if kind is str:
        return value.replace(key, PAINTED).replace("Authorization", PAINTED)
    if kind is int or kind is float or kind is bool or value is None:
        return value
    if kind is list:
        copy = [paint_out_key(item, key, depth + 1) for item in value]
        return OFF_ROAD if any(item is OFF_ROAD for item in copy) else copy
    if kind is not dict:
        return OFF_ROAD
    copy = {}
    for name, item in dict.items(value):
        if type(name) is not str:
            return OFF_ROAD
        if key in name or "Authorization" in name:
            continue
        item = paint_out_key(item, key, depth + 1)
        if item is OFF_ROAD:
            return OFF_ROAD
        copy[name] = item
    return copy
