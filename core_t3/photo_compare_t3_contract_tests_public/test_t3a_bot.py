import hashlib

import photo_compare_t3_candidate as c


class Session:
    def __init__(self, cleanup_error=False):
        self.reference = b"reference-secret-photo"
        self.candidate = b"candidate-secret-photo"
        self.deleted = 0
        self.cleanup_error = cleanup_error

    def photos(self):
        return self.reference, self.candidate

    def delete_all(self):
        self.deleted += 1
        if self.cleanup_error:
            raise RuntimeError("cleanup failed")
        self.reference = self.candidate = None


class Logger:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


def flattened(value):
    return repr(value)


def test_success_is_one_call_and_cleanup():
    session, logger, calls = Session(), Logger(), []

    def compare(reference, candidate):
        calls.append((reference, candidate))
        return {"status": "MATCH", "private": "must-not-escape"}

    result = c.run_bot_pair(session, compare, logger)
    assert result == {"schema_version": "photo-bot-result/1", "status": "MATCH"}
    assert calls == [(b"reference-secret-photo", b"candidate-secret-photo")]
    assert session.deleted == 1
    evidence = flattened([result, logger.events])
    assert "reference-secret-photo" not in evidence
    assert "candidate-secret-photo" not in evidence
    for raw in (b"reference-secret-photo", b"candidate-secret-photo"):
        assert hashlib.sha256(raw).hexdigest() not in evidence


def test_failures_are_unavailable_and_cleanup():
    for behavior in ("timeout", "provider", "unexpected", "malformed"):
        session, logger, calls = Session(), Logger(), []

        def compare(reference, candidate, behavior=behavior):
            calls.append(1)
            if behavior == "timeout":
                raise TimeoutError("candidate-secret-photo")
            if behavior == "provider":
                raise c.ProviderUnavailable("RAW-SECRET")
            if behavior == "unexpected":
                raise ValueError("RAW-SECRET candidate-secret-photo")
            return {"status": "SURELY_FINE"}

        result = c.run_bot_pair(session, compare, logger)
        assert result == {"schema_version": "photo-bot-result/1", "status": "UNAVAILABLE"}
        assert len(calls) == 1 and session.deleted == 1
        assert "candidate-secret-photo" not in flattened([result, logger.events])
        assert "RAW-SECRET" not in flattened([result, logger.events])


def test_cleanup_failure_never_returns_a_verdict():
    session = Session(cleanup_error=True)
    try:
        c.run_bot_pair(session, lambda _a, _b: {"status": "MATCH"}, Logger())
    except RuntimeError as error:
        assert str(error) == "cleanup failed"
    else:
        raise AssertionError("cleanup failure returned a result")
