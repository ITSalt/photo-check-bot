"""Mutation check for spend_cap.py: each mutant must make the suite fail. python3 tests/mutation/spend_cap_mutants.py"""
import json, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository root
SRC = open(os.path.join(HERE, "app", "spend_cap.py")).read()
MUTANTS = [
    # S1 under-counting mutants (P4, theone). Each changes one accounting rule.
    ('settle repeat never books an increase',
     '                    booked = high - m.usd',
     '                    booked = 0'),
    ('settle expired books nothing',
     '                    elif m is not None and m.kind == "expired":\n                        out = "expired"',
     '                    elif m is not None and m.kind == "expired":\n                        out, booked = "expired", 0'),
    ('settle unknown books nothing',
     '                    out, booked, high = "unknown", amount, amount',
     '                    out, booked, high = "unknown", amount, amount\n                    if h is None and m is None:\n                        booked = 0'),
    ('expired id remembered as released',
     '            self._remember(hid, "expired")',
     '            self._remember(hid, "released")'),
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

RUNNER = r'''
import json, sys, unittest

def in_body(test, err):
    # Loader placeholders raise through _callTestMethod but are not real tests.
    if isinstance(test, unittest.loader._FailedTest):
        return False
    # CPython 3.12 unittest: ordinary failures unwind this frame; subTests
    # report while it is still active. Match the test to exclude nested runs.
    code = unittest.TestCase._callTestMethod.__code__
    tb = err[2]
    while tb is not None:
        if tb.tb_frame.f_code is code and tb.tb_frame.f_locals.get("self") is test:
            return True
        tb = tb.tb_next
    frame = sys._getframe()
    while frame is not None:
        if frame.f_code is code and frame.f_locals.get("self") is test:
            return True
        frame = frame.f_back
    return False

class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixture_issues = []
        self.body_failures = []
        self.body_errors = []

    def addFailure(self, test, err):
        if in_body(test, err):
            self.body_failures.append(test.id())
        else:
            self.fixture_issues.append(test.id())
        super().addFailure(test, err)

    def addError(self, test, err):
        if in_body(test, err):
            self.body_errors.append(test.id())
        else:
            self.fixture_issues.append(test.id())
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            if not in_body(test, err):
                self.fixture_issues.append(subtest.id())
            elif issubclass(err[0], test.failureException):
                self.body_failures.append(subtest.id())
            else:
                self.body_errors.append(subtest.id())
        super().addSubTest(test, subtest, err)

suite = unittest.defaultTestLoader.discover("tests", pattern="test_spend_cap.py", top_level_dir=".")
result = unittest.TextTestRunner(verbosity=0, resultclass=Result).run(suite)
with open("result.json", "w") as f:
    json.dump({"run": result.testsRun, "skipped": len(result.skipped),
               "failures": [t.id() for t, _ in result.failures],
               "errors": [t.id() for t, _ in result.errors],
               "body_failures": result.body_failures,
               "body_errors": result.body_errors,
               "fixtures": result.fixture_issues + (["load tests.test_spend_cap"]
                            if unittest.defaultTestLoader.errors else []),
               "unexpected": [t.id() for t in result.unexpectedSuccesses]}, f)
sys.exit(not result.wasSuccessful())
'''


def run_suite(source, test_file, timeout=300):
    with tempfile.TemporaryDirectory() as d:
        for package in ("app", "tests"):
            os.makedirs(os.path.join(d, package))
            open(os.path.join(d, package, "__init__.py"), "w").close()
        with open(os.path.join(d, "app", "spend_cap.py"), "w") as f:
            f.write(source)
        shutil.copy(test_file, os.path.join(d, "tests", "test_spend_cap.py"))
        try:
            r = subprocess.run([sys.executable, "-c", RUNNER], cwd=d,
                               capture_output=True, text=True, timeout=timeout)
            with open(os.path.join(d, "result.json")) as f:
                report = json.load(f)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            return "BROKEN (runner)", type(e).__name__
        names = "; ".join(key + "=" + ", ".join(report[key])
                          for key in ("failures", "errors", "fixtures", "unexpected") if report[key])
        if r.returncode not in (0, 1) or bool(r.returncode) != bool(
                report["failures"] or report["errors"] or report["unexpected"]):
            return "BROKEN (runner)", names or "inconsistent exit/result"
        # Valid body witnesses establish a kill even in a mixed run.
        # Keep fixture IDs above so a kill never hides a fixture fault.
        if report["body_errors"]:
            return "killed (error)", names
        if report["body_failures"]:
            return "killed", names
        if report["fixtures"]:
            return "BROKEN (setup)", names
        if report["errors"] or report["unexpected"]:
            return "BROKEN (test error)", names
        if report["run"] <= report["skipped"]:
            return "BROKEN (no tests)", "no non-skipped tests ran"
        return "SURVIVED", ""


test_file = os.path.join(HERE, "tests", "test_spend_cap.py")
status, detail = run_suite(SRC, test_file)
if status != "SURVIVED":
    print("BROKEN (baseline): %s  %s" % (status, detail))
    sys.exit(1)
print("baseline OK")
bad = 0
for name, a, b in MUTANTS:
    if SRC.count(a) != 1:
        print("SKIP (pattern count %d): %s" % (SRC.count(a), name)); bad += 1; continue
    status, detail = run_suite(SRC.replace(a, b), test_file)
    bad += status not in ("killed", "killed (error)")
    print("%s  %s%s" % (status, name, ("  " + detail) if detail else ""))
print("mutants %d, not killed or skipped %d" % (len(MUTANTS), bad))
sys.exit(bool(bad))
