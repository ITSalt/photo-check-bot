import photo_compare_t3_candidate as c


class Logger:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


def test_missing_key_fails_before_transport():
    calls = []
    try:
        c.call_provider({"x": 1}, lambda *args, **kwargs: calls.append(1), {}, Logger())
    except c.ProviderUnavailable as error:
        assert str(error) == "MISSING_KEY"
    else:
        raise AssertionError("missing key did not fail")
    assert calls == []


def test_key_is_environment_only_and_redacted():
    calls, logger = [], Logger()

    def transport(request, headers, timeout_seconds):
        calls.append((request, headers, timeout_seconds))
        return {"status": 200, "body": {"ok": True}}

    result = c.call_provider(
        {"prompt": "compare"}, transport, {"VISION_API_KEY": "top-secret-key"}, logger
    )
    assert result == {"ok": True}
    assert len(calls) == 1 and calls[0][1]["Authorization"] == "Bearer top-secret-key"
    assert isinstance(calls[0][2], (int, float)) and 0 < calls[0][2] <= 120
    visible = repr([result, logger.events])
    assert "top-secret-key" not in visible and "Authorization" not in visible


def test_transport_failures_are_typed_and_redacted():
    cases = [
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("top-secret-key")),
        lambda *_args, **_kwargs: {"status": 503, "body": "top-secret-key"},
        lambda *_args, **_kwargs: {"status": 200, "body": "not-an-object"},
    ]
    for transport in cases:
        logger = Logger()
        try:
            c.call_provider({}, transport, {"VISION_API_KEY": "top-secret-key"}, logger)
        except c.ProviderUnavailable as error:
            assert str(error) in {"TIMEOUT", "HTTP_ERROR", "MALFORMED_BODY"}
        else:
            raise AssertionError("transport failure was accepted")
        assert "top-secret-key" not in repr(logger.events)
