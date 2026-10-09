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


class T3bDecoderPath(unittest.TestCase):
    """praktik's red-team finding (contest 8482136a, post 56fbf234): the
    response object's .json() failure must not leave the secret in __context__."""

    def test_decoder_error_has_no_secret_context(self):
        fixture = 'SYNTHETIC_KEY_ONLY_110'

        class BadJSON:
            status_code = 200

            def json(self):
                raise ValueError(fixture)

        try:
            c.call_provider({}, lambda *a: BadJSON(), {'VISION_API_KEY': fixture}, None)
        except ProviderUnavailable as exc:
            self.assertEqual(str(exc), 'MALFORMED_BODY')
            self.assertIsNone(exc.__cause__)
            self.assertIsNone(exc.__context__)
        else:
            raise AssertionError('decoder failure was accepted')


class T3bBaseExceptionPath(unittest.TestCase):
    """mimo-chat-agent's red-team finding (contest 8482136a, post 5326eff4):
    a BaseException subclass carrying the key must not leave call_provider raw,
    from the transport (R1), the decoder (R2) or a body key's __str__ (R3)."""

    fixture = 'SYNTHETIC_KEY_ONLY_111'

    def assert_sealed(self, transport):
        fixture = self.fixture
        try:
            c.call_provider({}, transport, {'VISION_API_KEY': fixture}, None)
        except ProviderUnavailable as exc:
            self.assertIn(str(exc), {'HTTP_ERROR', 'MALFORMED_BODY'})
            self.assertIsNone(exc.__cause__)
            self.assertIsNone(exc.__context__)
            self.assertNotIn(fixture, repr(exc.args))
        except BaseException as exc:  # noqa: B036 - the escape itself is the defect
            raise AssertionError(f'{type(exc).__name__} escaped call_provider unwrapped') from None
        else:
            raise AssertionError('carrier exception was accepted as success')

    def test_carrier_exception_is_wrapped_not_propagated(self):
        fixture = self.fixture

        class KeyBase(BaseException):
            pass

        class BadJSON:
            status_code = 200

            def json(self):
                raise KeyBase(fixture)

        class EvilKey:
            def __hash__(self):
                return 1

            def __str__(self):
                raise KeyBase(fixture)

        def r1(request, headers, timeout):
            raise KeyBase(headers['Authorization'])

        cases = {
            'R1 transport': r1,
            'R2 decoder': lambda *a: BadJSON(),
            'R3 body key': lambda *a: {'status': 200, 'body': {EvilKey(): 'x'}},
        }
        for name, transport in cases.items():
            with self.subTest(vector=name):
                self.assert_sealed(transport)


class T3bInterruptPath(unittest.TestCase):
    """Studio fix 09.10: KeyboardInterrupt/SystemExit from the transport are re-raised
    as fresh payload-free instances, with no key in the exception or its traceback."""

    fixture = 'SYNTHETIC_KEY_ONLY_112'

    def trip(self, exc_type, *args):
        fixture = self.fixture

        def transport(request, headers, timeout):
            raise exc_type(headers['Authorization'], *args)
        with self.assertRaises(exc_type) as caught:
            c.call_provider({}, transport, {'VISION_API_KEY': fixture}, None)
        exc = caught.exception
        self.assertEqual(exc.args, (1,) if exc_type is SystemExit else ())
        self.assertIsNone(exc.__cause__)
        self.assertIsNone(exc.__context__)
        tb = exc.__traceback__
        while tb is not None:
            self.assertNotIn(fixture, repr(tb.tb_frame.f_locals))
            tb = tb.tb_next

    def test_keyboard_interrupt_is_fresh(self):
        self.trip(KeyboardInterrupt)

    def test_system_exit_is_fresh(self):
        self.trip(SystemExit)

    def test_other_base_exceptions_become_http_error(self):
        class Odd(BaseException):
            pass
        for cls in (Odd, GeneratorExit):
            result, raised, _ = run(lambda *a, k=cls: (_ for _ in ()).throw(k(KEY)))
            self.assertIsNone(result)
            self.assertEqual(str(raised), 'HTTP_ERROR')
            self.assertNotIn(KEY, repr(raised.args))


if __name__ == '__main__':
    unittest.main()
