import json
import unittest

from .common import response, sut


class T2CProvider(unittest.TestCase):
    def test_fake_transport_once_and_typed_failures(self):
        calls = []
        good = response()

        def transport(request):
            calls.append(request)
            return {"status": 200, "body": json.dumps(good)}

        self.assertEqual(sut.provider_call(transport, {"x": 1}), good)
        self.assertEqual(calls, [{"x": 1}])
        cases = [
            (lambda _request: (_ for _ in ()).throw(TimeoutError()), "TIMEOUT"),
            (lambda _request: {"status": 403, "body": ""}, "REFUSED"),
            (lambda _request: {"status": 200, "body": "{"}, "MALFORMED_JSON"),
        ]
        for fake, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(sut.ProviderError) as caught:
                    sut.provider_call(fake, {})
                self.assertEqual(caught.exception.code, code)
