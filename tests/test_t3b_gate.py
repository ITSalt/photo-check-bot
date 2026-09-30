"""T3b acceptance gate beyond the public tests.

Clause 1 (clean exception object, no key via __context__/__cause__) by Nascens
(agent-277e27cc-c99, getpostingboard.dev 86b24834); other cases from the
orchestrator's red-team run on antigravity's entries (contest 8482136a).
"""
import sys
import traceback
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core_t3'))
import photo_compare_t3_candidate as c  # noqa: E402
from app.photo_compare import ProviderUnavailable  # noqa: E402

KEY = 'top-secret-key'
ENV = {'VISION_API_KEY': KEY}


def run(transport):
    log = []
    try:
        return c.call_provider({'prompt': 'x'}, transport, ENV, log.append), None, log
    except ProviderUnavailable as error:
        return None, error, log


def raising(error):
    def transport(*_args, **_kwargs):
        raise error
    return transport


class T3bGate(unittest.TestCase):
    def test_exception_object_carries_no_key(self):
        for error in (TimeoutError(KEY), ValueError(KEY), OSError(KEY)):
            with self.subTest(error=type(error).__name__):
                _, raised, log = run(raising(error))
                self.assertIsNotNone(raised)
                self.assertIsNone(raised.__cause__)
                self.assertIsNone(raised.__context__)
                text = ''.join(traceback.format_exception(type(raised), raised, raised.__traceback__))
                self.assertNotIn(KEY, text + repr(raised) + repr(log))

    def test_raises_the_bots_exception_type(self):
        _, raised, _ = run(raising(TimeoutError('t')))
        self.assertIsInstance(raised, ProviderUnavailable)
        self.assertEqual(str(raised), 'TIMEOUT')

    def test_echoed_key_is_scrubbed(self):
        result, raised, _ = run(lambda *a: {'status': 200, 'body': {
            'echo': 'Bearer ' + KEY, 'Authorization': 'Bearer ' + KEY, 'nested': [KEY]}})
        self.assertIsNone(raised)
        self.assertNotIn(KEY, repr(result))
        self.assertNotIn('Authorization', repr(result))

    def test_malformed_envelopes_fail_closed(self):
        for response in ({'status': 200}, {'ok': True}, {'status': '200', 'body': {'a': 1}}):
            with self.subTest(response=response):
                result, raised, _ = run(lambda *a, r=response: r)
                self.assertIsNone(result)
                self.assertIn(str(raised), {'MALFORMED_BODY', 'HTTP_ERROR'})


if __name__ == '__main__':
    unittest.main()
