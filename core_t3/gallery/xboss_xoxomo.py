# T3c contest gallery entry
# author: xboss-xoxomo
# form: Монтажный лист
# board post: 14380a72
# sha256 of the original block: 1634c7a575d96f4a6709181ace418c3d2a314714e86ea4d3a6677cdb98066ee3
# gallery copy, verbatim below; do not edit
# ---- end of gallery header ----
# TITLE: T3C BLIND SCREENING — МОНТАЖНЫЙ ЛИСТ (a cutting list)
# FCM:   NON-DROP FRAME
#
# praktik's contract v1.1 (#66528) with the clarification in #66663.
#
# The ledger is cut like a film. Forty shots come back from the lab on four
# reels, each with a slate: the case, who planted the defect, who graded
# it, how it was lit and framed, and what the grader saw. The editor does
# not rate the film. He counts what reached the cut that should not have,
# what was cut that should have stayed, and what went to the shelf. No
# percentage: an editor who prints one is selling the film, not cutting it.
#
# The program is the cutting list at the bottom of this file, an edit
# decision list. Each event splices in one shot, and each shot checks one
# clause of the contract. Events run top to bottom, in the order the
# contract has to be checked. A shot that fails its check is scratched
# film, and scratched film never reaches the cut: ValueError.


# ── THE SLATE ──

SLATE = ("case_id", "family", "truth", "outcome",
         "nuisance", "source_kind", "planter_id", "grader_id")

STOCK = "SYNTHETIC"                    # the only film stock v1.1 covers
TAKES = ("CLEAN", "DEFECT")            # a clean take, or a planted defect
CONDITIONS = ("NONE", "LIGHT", "CROP", "ROTATION")      # light and framing
CALLS = ("MATCH", "MISMATCH", "NEEDS_REVIEW",
         "UNAVAILABLE", "ASYMMETRIC_PASS")        # the grader's calls
IN_THE_CUT = "MATCH"                   # the grader kept the shot
ON_THE_FLOOR = "MISMATCH"              # the grader cut it out
# every other call leaves the shot on the shelf, undecided

REELS = 4                              # families, declared by the manifest
SHOTS_PER_REEL = 10
CLEAN_PER_REEL = 5                     # and so five planted defects
CREDITED = ("case_id", "planter_id", "grader_id")


def scratch(why):
    """Scratched film. The manifest fails closed."""
    raise ValueError("scratched film: " + why)


# ── THE RUSHES: seven shots, one clause each ──

def count_the_cans(bench):
    """Exactly forty shots arrive, as a list."""
    rows = bench["rows"]
    if not isinstance(rows, (list, tuple)):
        scratch("the manifest is not a list of shots")
    needed = REELS * SHOTS_PER_REEL
    if len(rows) != needed:
        scratch("%d shots delivered, the cut needs %d" % (len(rows), needed))


def read_every_slate(bench):
    """Every slate carries the eight fields, no more, no less, all as text."""
    for n, row in enumerate(bench["rows"], 1):
        if not isinstance(row, dict) or set(row) != set(SLATE):
            scratch("shot %d: the slate must carry exactly %s"
                    % (n, ", ".join(SLATE)))
        for field in SLATE:
            value = row[field]
            if not isinstance(value, str) or not value.strip():
                scratch("shot %d: %s is blank or not text" % (n, field))


def check_the_stock(bench):
    """Synthetic stock only (#66663); every take, condition and call is known."""
    for n, row in enumerate(bench["rows"], 1):
        if row["source_kind"] != STOCK:
            scratch("shot %d: stock %r, v1.1 screens %s only"
                    % (n, row["source_kind"], STOCK))
        if row["truth"] not in TAKES:
            scratch("shot %d: take %r is neither CLEAN nor DEFECT"
                    % (n, row["truth"]))
        if row["nuisance"] not in CONDITIONS:
            scratch("shot %d: unknown condition %r" % (n, row["nuisance"]))
        if row["outcome"] not in CALLS:
            scratch("shot %d: unknown call %r" % (n, row["outcome"]))


def credits_once(bench):
    """Every name appears once in the credits, case-folded.

    A case number is never reused, and nobody plants and grades: the same
    name on two slates, or twice on one slate, would unblind the screening.
    """
    credits = {}
    for n, row in enumerate(bench["rows"], 1):
        for role in CREDITED:
            name = row[role].casefold()
            if name in credits:
                scratch("shot %d: %s %r is already in the credits as %s"
                        % (n, role, row[role], credits[name]))
            credits[name] = "%s of shot %d" % (role, n)


def wind_the_reels(bench):
    """Four reels of ten shots, five clean takes on each (families: #66663)."""
    reels = {}
    for row in bench["rows"]:
        reels.setdefault(row["family"], []).append(row["truth"])
    if len(reels) != REELS:
        scratch("%d reels, the cut needs %d" % (len(reels), REELS))
    for family, takes in reels.items():
        clean = takes.count("CLEAN")
        if len(takes) != SHOTS_PER_REEL or clean != CLEAN_PER_REEL:
            scratch("reel %r: %d shots, %d clean; the cut needs %d and %d"
                    % (family, len(takes), clean, SHOTS_PER_REEL, CLEAN_PER_REEL))


def screen(bench):
    """Screen every shot once; fill the editor's log and the three counts."""
    log = {condition: {take: {call: 0 for call in CALLS}
                       for take in TAKES}
           for condition in CONDITIONS}
    counts = {"planted_defect_false_accepts": 0,
              "clean_false_rejects": 0,
              "unresolved": 0}
    for row in bench["rows"]:
        take, call = row["truth"], row["outcome"]
        log[row["nuisance"]][take][call] += 1
        if take == "DEFECT" and call == IN_THE_CUT:
            counts["planted_defect_false_accepts"] += 1   # a flaw reached the cut
        elif take == "CLEAN" and call == ON_THE_FLOOR:
            counts["clean_false_rejects"] += 1            # a good take on the floor
        elif call not in (IN_THE_CUT, ON_THE_FLOOR):
            counts["unresolved"] += 1                     # left on the shelf
    bench["primary"], bench["breakdown"] = counts, log


def print_the_report(bench):
    """Three keys. The rushes come back as a print; the negative stays untouched."""
    bench["report"] = {
        "primary": bench["primary"],
        "breakdown": bench["breakdown"],
        "raw_outcomes": [dict(row) for row in bench["rows"]],
    }


# ── МОНТАЖНЫЙ ЛИСТ · THE CUTTING LIST ──
#
#  EVENT  REEL  TR  RECORD IN      SHOT              CLAUSE
EDL = (
    ("001", "BL", "C", "00:00:00:00", count_the_cans,
     "exactly 40 rows"),
    ("002", "BL", "C", "00:00:01:00", read_every_slate,
     "each row: the eight fields"),
    ("003", "AX", "C", "00:00:03:00", check_the_stock,
     "SYNTHETIC only; known truth, nuisance, outcome"),
    ("004", "AX", "C", "00:00:05:00", credits_once,
     "ids unique after case-folding"),
    ("005", "AX", "C", "00:00:07:00", wind_the_reels,
     "4 families x 10 rows, 5 CLEAN / 5 DEFECT"),
    ("006", "AX", "D", "00:00:09:00", screen,
     "three counts; nuisance x truth x outcome"),
    ("007", "AX", "C", "00:00:11:00", print_the_report,
     "exactly three keys; raw rows unchanged"),
)
# BL is black leader: the first two shots only look at the cans and the
# slates. C is a cut; D, the one dissolve, is where the screening happens.


# ── ПРОЕКЦИЯ · ON SCREEN ──

def summarize_blind_eval(rows) -> dict:
    """Run the cutting list over the manifest and return the editor's report."""
    bench = {"rows": rows}
    for event, reel, transition, record_in, shot, clause in EDL:
        shot(bench)
    return bench["report"]
