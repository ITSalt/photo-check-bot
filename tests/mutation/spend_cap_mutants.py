"""Mutation check for spend_cap.py: each mutant must make the suite fail. python3 tests/mutation/spend_cap_mutants.py"""
import os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository root
SRC = open(os.path.join(HERE, "app", "spend_cap.py")).read()
MUTANTS = [
    ("v2 hold read as taken at the start of its day",
     "timestamp() + 86400\n", "timestamp()\n"),
    ("v2 hold persisted with an at outside its day",
     '            if h.at is not None:                 # a v2 hold is written back as the old owner wrote it\n                holds[h.id]["at"] = h.at',
     '            holds[h.id]["at"] = _taken_at(h)'),
    ("orphan expiry ignores the lifetime", "now - _taken_at(h) > self._lifetime", "True"),
    ("orphan expiry >= instead of >", "now - _taken_at(h) > self._lifetime", "now - _taken_at(h) >= self._lifetime"),
    ("orphan of today can expire", "h.orphan and h.day < today and", "h.orphan and"),
    ("at not checked against day on load", "_utc_day(at) != hday", "False"),
    ("two clock reads in reserve", "at=self._now)", "at=self._clock())"),
    ("None lifetime expires orphans", "        if self._lifetime is None:\n            return", "        if False:\n            return"),
    ("future ledger day trusted", "spent is None or day > today", "spent is None"),
    ("orphan not counted", "sum(h.left for h in self._holds.values())", "sum(h.left for h in self._holds.values() if not h.orphan)"),
]
bad = 0
for name, a, b in MUTANTS:
    if SRC.count(a) != 1:
        print("SKIP (pattern count %d): %s" % (SRC.count(a), name)); bad += 1; continue
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "app")); os.makedirs(os.path.join(d, "tests"))
    open(os.path.join(d, "app", "spend_cap.py"), "w").write(SRC.replace(a, b))
    shutil.copy(os.path.join(HERE, "tests", "test_spend_cap.py"), os.path.join(d, "tests"))
    r = subprocess.run([sys.executable, "-m", "unittest", "-q", "tests.test_spend_cap"], cwd=d, capture_output=True, text=True, timeout=300)
    killed = r.returncode != 0
    bad += not killed
    print("%s  %s" % ("killed  " if killed else "SURVIVED", name))
    shutil.rmtree(d)
print("mutants %d, survived or skipped %d" % (len(MUTANTS), bad))
