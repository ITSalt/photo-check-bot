"""T4 tests: fake transports only. No network, no keys.

FakeCallProvider is a TEST DOUBLE of the T3b boundary (not the real call_provider,
which board authors supply). It follows the public T3b contract: key from
environ["VISION_API_KEY"] only, one transport call, typed ProviderUnavailable codes.
"""
import json
import logging
import urllib.error
import unittest
from unittest.mock import patch

from app.photo_compare import ProviderUnavailable, run_bot_pair
from app import vision_compare, vision_request, vision_transport
from app.vision_request import ImageRejected, build_request, parse_verdict
from app.vision_transport import TransportConfigError, make_transport

JPEG = b"\xff\xd8\xff\xe0" + b"SECRETJPEGBYTES" * 4
PNG = b"\x89PNG\r\n\x1a\n" + b"SECRETPNGBYTES" * 4
WEBP = b"RIFF\x10\x00\x00\x00WEBP" + b"SECRETWEBPBYTES"
KEY = "sk-top-secret-key"
URL = "https://openrouter.ai/api/v1/chat/completions"
ENV = {"VISION_BASE_URL": URL, "VISION_MODEL": "google/gemini-3.6-flash",
       "VISION_HOST_ALLOWLIST": "openrouter.ai", "VISION_API_KEY": KEY}


def fake_call_provider(request, transport, environ, logger):
    """TEST DOUBLE of T3b (see module docstring)."""
    key = environ.get("VISION_API_KEY")
    if not key:
        raise ProviderUnavailable("MISSING_KEY")
    try:
        reply = transport(request, {"Authorization": "Bearer " + key}, 30)
    except TimeoutError:
        raise ProviderUnavailable("TIMEOUT") from None
    if reply.get("status") != 200:
        raise ProviderUnavailable("HTTP_ERROR")
    if not isinstance(reply.get("body"), dict):
        raise ProviderUnavailable("MALFORMED_BODY")
    return reply["body"]


def answer(verdict="MATCH", reasons=("same logo",), usage=None, **choice):
    body = {"choices": [{"message": {"content": json.dumps({"verdict": verdict, "reasons": list(reasons)})},
                         "finish_reason": "stop", **choice}]}
    if usage is not None:
        body["usage"] = usage
    return body


class FakeResponse:
    def __init__(self, status=200, data=b"{}"):
        self.status, self.data, self.closed = status, data, False

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.deleted = 0

    def photos(self):
        return JPEG, PNG

    def delete_all(self):
        self.deleted += 1


class RequestTests(unittest.TestCase):
    def test_shape(self):
        req = build_request(JPEG, PNG, "m/x")
        self.assertEqual(req["model"], "m/x")
        self.assertEqual(req["temperature"], 0)
        self.assertEqual(req["response_format"], {"type": "json_object"})
        self.assertEqual(req["reasoning"], {"effort": "minimal"})
        parts = req["messages"][0]["content"]
        urls = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
        self.assertEqual(len(urls), 2)
        self.assertTrue(urls[0].startswith("data:image/jpeg;base64,"))
        self.assertTrue(urls[1].startswith("data:image/png;base64,"))
        self.assertNotEqual(urls[0], urls[1])
        text = parts[0]["text"]
        for word in ("shape", "colour", "logo", "proportions", "details", "background",
                     "lighting", "angle", "NEEDS_REVIEW", "reasons"):
            self.assertIn(word, text)
        # reference first, candidate second
        self.assertLess(parts.index({"type": "image_url", "image_url": {"url": urls[0]}}),
                        parts.index({"type": "image_url", "image_url": {"url": urls[1]}}))

    def test_webp_accepted_and_roundtrip(self):
        import base64
        req = build_request(WEBP, JPEG, "m")
        url = req["messages"][0]["content"][2]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/webp;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), WEBP)  # not re-encoded

    def test_rejections(self):
        for bad, code in ((b"GIF89a....", "NOT_IMAGE"), (b"%PDF-1.4", "NOT_IMAGE"), (b"", "EMPTY"),
                          (None, "EMPTY"), ("text", "EMPTY"), (b"RIFF\0\0\0\0WAVE", "NOT_IMAGE")):
            for pos in (0, 1):
                args = [JPEG, JPEG]
                args[pos] = bad
                with self.assertRaises(ImageRejected) as cm:
                    build_request(*args, "m")
                self.assertEqual(cm.exception.args[0], code)

    def test_size_cap_boundary(self):
        big = JPEG + b"\0" * 100
        build_request(big, JPEG, "m", max_image_bytes=len(big))
        with self.assertRaises(ImageRejected) as cm:
            build_request(big, JPEG, "m", max_image_bytes=len(big) - 1)
        self.assertEqual(cm.exception.args[0], "TOO_LARGE")
        with self.assertRaises(ImageRejected):
            build_request(JPEG, big, "m", max_image_bytes=len(big) - 1)
        self.assertEqual(vision_request.MAX_IMAGE_BYTES, 8 * 1024 * 1024)

    def test_error_text_has_no_bytes(self):
        try:
            build_request(b"GIF89a" + b"PRIVATE", JPEG, "m")
        except ImageRejected as exc:
            self.assertNotIn("PRIVATE", repr(exc))

    def test_model_required(self):
        for m in ("", "  ", None):
            with self.assertRaises(ValueError):
                build_request(JPEG, PNG, m)


class ParseTests(unittest.TestCase):
    def test_verdicts(self):
        for v in ("MATCH", "MISMATCH", "NEEDS_REVIEW"):
            self.assertEqual(parse_verdict(answer(v, ("a", "b"))), {"status": v, "reasons": ["a", "b"]})
        self.assertEqual(parse_verdict(answer("MATCH", ()))["status"], "MATCH")

    def test_vocabulary_is_subset_of_t2(self):
        from core_t2.photo_compare_t2_candidate import AGGREGATE_OUTCOMES
        self.assertTrue(set(vision_request.VERDICTS) <= set(AGGREGATE_OUTCOMES))

    def test_unavailable_cases(self):
        def content(c, **kw):
            return {"choices": [{"message": {"content": c}, **kw}]}
        cases = [None, [], "x", {}, {"choices": []}, {"choices": [None]}, {"choices": [{}]},
                 {"choices": [{"message": None}]}, content(None), content(""), content("  "),
                 content("not json"), content("[]"), content('"MATCH"'), content("{}"),
                 content('{"verdict":"match","reasons":[]}'), content('{"verdict":"ASYMMETRIC_PASS","reasons":[]}'),
                 content('{"verdict":["MATCH"],"reasons":[]}'), content('{"verdict":"UNAVAILABLE","reasons":[]}'),
                 content('{"verdict":"MATCH","reasons":[]}', finish_reason="length"),
                 content('{"verdict":"MATCH","reasons":[]}', finish_reason="content_filter"),
                 content(' NaN'), content('MATCH')]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(parse_verdict(body)["status"], "UNAVAILABLE")

    def test_sloppy_answer_never_gives_match_or_mismatch(self):
        for verdict in ("MATCH", "MISMATCH"):
            for extra in ('"reasons":"same"', '"reasons":[1]', '"reasons":[""]', '"reasons":[' + '"a",' * 8 + '"a"]',
                          '"reasons":["' + "x" * 201 + '"]', '"reasons":[],"extra":1', '"x":1'):
                body = {"choices": [{"message": {"content": '{"verdict":"%s",%s}' % (verdict, extra)}}]}
                with self.subTest(extra=extra):
                    self.assertEqual(parse_verdict(body)["status"], "NEEDS_REVIEW")
        body = {"choices": [{"message": {"content": '{"verdict":"MATCH"}'}}]}
        self.assertEqual(parse_verdict(body)["status"], "NEEDS_REVIEW")

    def test_reason_limits_accepted_at_boundary(self):
        self.assertEqual(parse_verdict(answer("MATCH", ["x" * 200] * 8))["status"], "MATCH")


class TransportTests(unittest.TestCase):
    def make(self, resp=None, exc=None, **kw):
        seen = []

        def opener(req, timeout):
            seen.append((req, timeout))
            if exc:
                raise exc
            return resp
        kw.setdefault("host_allowlist", {"openrouter.ai"})
        return make_transport(URL, 60, opener=opener, **kw), seen

    def test_success_and_request_building(self):
        resp = FakeResponse(200, b'{"a": 1}')
        t, seen = self.make(resp)
        out = t({"model": "m"}, {"Authorization": "Bearer k"}, 20)
        self.assertEqual(out, {"status": 200, "body": {"a": 1}})
        req, timeout = seen[0]
        self.assertEqual(timeout, 20.0)
        self.assertEqual(req.full_url, URL)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"model": "m"})
        self.assertEqual(req.get_header("Authorization"), "Bearer k")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertTrue(resp.closed)

    def test_timeout_capped(self):
        t, seen = self.make(FakeResponse())
        t({}, {}, 500)
        self.assertEqual(seen[0][1], 60.0)

    def test_bad_call_timeout(self):
        t, _ = self.make(FakeResponse())
        for bad in (0, -1, None, "5", True):
            with self.assertRaises(TransportConfigError):
                t({}, {}, bad)

    def test_host_allowlist_and_url_rules(self):
        bad_urls = ["http://openrouter.ai/x", "ftp://openrouter.ai/", "https://evil.example/x",
                    "https://openrouter.ai.evil.example/x", "https://user:pw@openrouter.ai/x",
                    "https://openrouter.ai:8443/x", "https://openrouter.ai:99999/x", "", None,
                    "//openrouter.ai/x", "https://openrouter.ai/x#f", "https:///x"]
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(TransportConfigError):
                make_transport(url, 30, host_allowlist={"openrouter.ai"})
        make_transport("https://OpenRouter.ai:443/x", 30, host_allowlist={"openrouter.ai"})
        with self.assertRaises(TransportConfigError):
            make_transport(URL, 30, host_allowlist=set())

    def test_config_messages_hide_url(self):
        try:
            make_transport("https://user:" + KEY + "@evil.example/", 30, host_allowlist={"openrouter.ai"})
        except TransportConfigError as exc:
            self.assertNotIn(KEY, repr(exc))

    def test_parse_allowlist(self):
        self.assertEqual(vision_transport.parse_allowlist(" A.com , b.org,"), frozenset({"a.com", "b.org"}))
        for bad in ("", " , ", None, "a.com/x", "a b", "-a.com"):
            with self.assertRaises(TransportConfigError):
                vision_transport.parse_allowlist(bad)

    def test_timeout_caps_validated(self):
        for bad in (0, -1, 121, None, True, "30"):
            with self.assertRaises(TransportConfigError):
                make_transport(URL, bad, host_allowlist={"openrouter.ai"})
        make_transport(URL, 120, host_allowlist={"openrouter.ai"})

    def test_redirect_refused_not_followed(self):
        for code in (301, 302, 303, 307, 308):
            resp = FakeResponse(code, b'{"choices": []}')
            t, seen = self.make(resp)
            self.assertEqual(t({}, {"Authorization": "Bearer k"}, 5), {"status": code, "body": None})
            self.assertEqual(len(seen), 1)  # one request, no second hop

    def test_real_opener_has_no_redirect_http_or_proxy_handlers(self):
        import urllib.request as ur
        handlers = vision_transport._default_opener().handlers
        self.assertFalse(any(isinstance(h, ur.HTTPRedirectHandler) for h in handlers))
        self.assertFalse(any(type(h) is ur.HTTPHandler for h in handlers))
        self.assertTrue(any(isinstance(h, ur.HTTPSHandler) for h in handlers))
        self.assertFalse(any(isinstance(h, ur.ProxyHandler) for h in handlers))

    def test_non_200_has_no_body(self):
        for code in (400, 401, 429, 500, 503):
            t, _ = self.make(FakeResponse(code, b'{"error": "KEY-ECHO"}'))
            self.assertEqual(t({}, {}, 5), {"status": code, "body": None})

    def test_response_cap(self):
        data = b'{"a": "' + b"x" * 100 + b'"}'
        t, _ = self.make(FakeResponse(200, data), response_cap=len(data))
        self.assertIsInstance(t({}, {}, 5)["body"], dict)
        t, _ = self.make(FakeResponse(200, data), response_cap=len(data) - 1)
        self.assertEqual(t({}, {}, 5), {"status": 200, "body": None})

    def test_malformed_bodies(self):
        for data in (b"not json", b"\xff\xfe", b"[1]", b'"x"', b"null", b""):
            t, _ = self.make(FakeResponse(200, data))
            self.assertEqual(t({}, {}, 5), {"status": 200, "body": None})

    def test_timeouts_pass_through(self):
        import socket
        for exc in (TimeoutError("x"), socket.timeout("x"), urllib.error.URLError(TimeoutError("x"))):
            t, _ = self.make(exc=exc)
            with self.assertRaises(TimeoutError):
                t({}, {}, 5)

    def test_network_errors_become_status(self):
        for exc in (urllib.error.URLError("dns " + KEY), ConnectionResetError(KEY), OSError(KEY)):
            t, _ = self.make(exc=exc)
            self.assertEqual(t({}, {}, 5), {"status": vision_transport.STATUS_NETWORK_ERROR, "body": None})


class ConfigTests(unittest.TestCase):
    def test_valid(self):
        cfg = vision_compare.load_config(ENV)
        self.assertEqual(cfg["model"], "google/gemini-3.6-flash")
        self.assertEqual(cfg["timeout"], vision_compare.DEFAULT_TIMEOUT)
        self.assertEqual(vision_compare.load_config({**ENV, "VISION_TIMEOUT": "12.5"})["timeout"], 12.5)

    def test_missing_keeps_old_path(self):
        self.assertIsNone(vision_compare.load_config({}))
        self.assertIsNone(vision_compare.compare_from_env({}))
        for k in ("VISION_BASE_URL", "VISION_MODEL", "VISION_HOST_ALLOWLIST", "VISION_API_KEY"):
            env = {a: b for a, b in ENV.items() if a != k}
            self.assertIsNone(vision_compare.compare_from_env(env, call_provider=fake_call_provider), k)
            self.assertIsNone(vision_compare.compare_from_env({**ENV, k: "  "}, call_provider=fake_call_provider), k)

    def test_partial_warning_names_no_values(self):
        with self.assertLogs("vision", "WARNING") as logs:
            vision_compare.load_config({k: v for k, v in ENV.items() if k != "VISION_MODEL"})
        text = repr(logs.output)
        self.assertIn("VISION_MODEL", text)
        self.assertNotIn(KEY, text)
        self.assertNotIn("openrouter", text)

    def test_invalid_raises(self):
        for patch_ in ({"VISION_MODEL": "bad model!"}, {"VISION_MODEL": "x" * 101}, {"VISION_TIMEOUT": "abc"},
                       {"VISION_TIMEOUT": "0"}, {"VISION_TIMEOUT": "-3"}, {"VISION_TIMEOUT": "121"},
                       {"VISION_TIMEOUT": "nan"}, {"VISION_BASE_URL": "http://openrouter.ai/x"},
                       {"VISION_BASE_URL": "https://evil.example/x"}, {"VISION_HOST_ALLOWLIST": "a b"}):
            with self.subTest(patch_=patch_), self.assertRaises(vision_compare.VisionConfigError):
                vision_compare.load_config({**ENV, **patch_})

    def test_no_t3b_available_disables(self):
        with patch.object(vision_compare, "_default_call_provider", return_value=None):
            self.assertIsNone(vision_compare.compare_from_env(ENV))

    def test_default_resolves_repo_stub_which_fails_closed(self):
        compare = vision_compare.compare_from_env(ENV, transport=lambda *a: self.fail("no network"))
        session = Session()
        self.assertEqual(run_bot_pair(session, compare, None)["status"], "UNAVAILABLE")
        self.assertEqual(session.deleted, 1)

    def test_bot_default_unchanged_without_config(self):
        import importlib, os
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "unit-test-placeholder"}):
            bot = importlib.import_module("app.bot")
        update = {"message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1},
                              "photo": [{"file_id": "x", "file_size": 10}]}}
        self.assertIsNone(vision_compare.compare_from_env({}))
        with patch.object(bot, "call") as send:
            bot.handle(update)
        self.assertEqual(send.call_args.args[1]["text"], bot.TEXTS["ru"]["fallback"])


class ChainTests(unittest.TestCase):
    def chain(self, body=None, status=200, exc=None, env=ENV):
        calls = []

        def transport(request, headers, timeout):
            calls.append((request, headers))
            if exc:
                raise exc
            return {"status": status, "body": body}
        compare = vision_compare.compare_from_env(env, call_provider=fake_call_provider, transport=transport)
        return compare, calls

    def run_pair(self, compare):
        session = Session()
        result = run_bot_pair(session, compare, None)
        self.assertEqual(session.deleted, 1)
        return result

    def test_match_and_mismatch(self):
        for verdict in ("MATCH", "MISMATCH", "NEEDS_REVIEW"):
            compare, calls = self.chain(answer(verdict))
            result = self.run_pair(compare)
            self.assertEqual(result, {"schema_version": "photo-bot-result/1", "status": verdict})
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0]["model"], "google/gemini-3.6-flash")
            self.assertEqual(calls[0][1]["Authorization"], "Bearer " + KEY)

    def test_failures_are_unavailable(self):
        for kw in ({"body": None, "status": 503}, {"body": None}, {"body": {}}, {"body": {"choices": "x"}},
                   {"exc": TimeoutError("t")}, {"body": answer("MATCH"), "status": 302},
                   {"body": answer("MATCH"), "env": {k: v for k, v in ENV.items() if k != "VISION_API_KEY"}}):
            with self.subTest(kw=kw):
                compare = self.chain(**kw)[0]
                if compare is None:  # key missing: disabled, the old path
                    continue
                self.assertEqual(self.run_pair(compare)["status"], "UNAVAILABLE")

    def test_non_image_never_reaches_transport(self):
        compare, calls = self.chain(answer("MATCH"))
        out = compare(b"%PDF-1.4 private", PNG)
        self.assertEqual(out["status"], "UNAVAILABLE")
        self.assertEqual(calls, [])

    def test_missing_key_double_fails_before_transport(self):
        calls = []
        config = vision_compare.load_config(ENV)
        compare = vision_compare.make_compare(config, {}, fake_call_provider, lambda *a: calls.append(1))
        self.assertEqual(compare(JPEG, PNG)["status"], "UNAVAILABLE")
        self.assertEqual(calls, [])

    def test_usage_logged(self):
        compare, _ = self.chain(answer("MATCH", usage={"prompt_tokens": 1200, "completion_tokens": 30, "cost": 0.0004}))
        with self.assertLogs("vision", "INFO") as logs:
            compare(JPEG, PNG)
        self.assertIn("vision_call model=google/gemini-3.6-flash usage_prompt=1200 usage_completion=30 cost=0.0004",
                      logs.output[0])

    def test_usage_absent_or_bad(self):
        for usage in (None, "x", {}, {"prompt_tokens": "9", "completion_tokens": True, "cost": float("nan")}):
            body = answer("MATCH")
            if usage is not None:
                body["usage"] = usage
            compare, _ = self.chain(body)
            with self.assertLogs("vision", "INFO") as logs:
                compare(JPEG, PNG)
            self.assertIn("usage_prompt=n/a usage_completion=n/a cost=n/a", logs.output[0])

    def test_logs_hold_no_secret_bytes_or_reasons(self):
        import base64
        reasons = ("PRIVATE-REASON-TEXT",)
        cases = [(answer("MATCH", reasons, usage={"prompt_tokens": 1}), {}),
                 ({"body": None, "status": 500}, {}), (None, {"exc": TimeoutError(KEY)})]
        for body, kw in cases:
            compare, _ = (self.chain(body) if not kw and body and "choices" in body else
                          self.chain(**(body if isinstance(body, dict) and "status" in body else {}), **kw))
            with self.assertLogs("vision", "DEBUG") as logs:
                logging.getLogger("vision").info("sentinel")
                compare(JPEG, PNG)
                compare(b"%PDF " + PNG, PNG)
            text = repr(logs.output)
            for secret in (KEY, "Authorization", "Bearer", "SECRETJPEGBYTES", "SECRETPNGBYTES", "PRIVATE-REASON-TEXT",
                           base64.b64encode(JPEG).decode(), base64.b64encode(JPEG).decode()[:16]):
                self.assertNotIn(secret, text)

    def test_provider_sink_filters_events(self):
        with self.assertLogs("vision", "INFO") as logs:
            vision_compare._provider_sink("TIMEOUT")
            vision_compare._provider_sink("Bearer " + KEY)
            vision_compare._provider_sink({"k": KEY})
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn(KEY, repr(logs.output))

    def test_smoke_main_without_config(self):
        self.assertEqual(vision_compare._main(["a", "b"], {}), 2)
        self.assertEqual(vision_compare._main([], ENV), 2)


if __name__ == "__main__":
    unittest.main()
