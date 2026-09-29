"""Author integration checks; public praktik T3a contract is unchanged."""
import importlib
import os
import unittest
from unittest.mock import Mock, patch

from app.photo_checks import PhotoChecks, MESSAGES
from core_t3.photo_compare_t3_candidate import run_bot_pair


class Session:
    def __init__(self):
        self.reference = b'synthetic-reference-private'
        self.candidate = b'synthetic-candidate-private'
        self.deleted = 0

    def photos(self):
        return self.reference, self.candidate

    def delete_all(self):
        self.deleted += 1
        self.reference = self.candidate = None


def event(fid, user=1, caption=None, **extra):
    return {'user_id': user, 'chat_id': user, 'kind': 'photo', 'file_id': fid,
            'caption': caption, **extra}


class BoundaryTests(unittest.TestCase):
    def test_exact_objects_all_statuses_and_no_private_output(self):
        for status in ('MATCH', 'MISMATCH', 'NEEDS_REVIEW', 'UNAVAILABLE'):
            with self.subTest(status=status):
                session, calls, logs = Session(), [], []
                ref, candidate = session.photos()
                def compare(a, b):
                    self.assertIs(a, ref)
                    self.assertIs(b, candidate)
                    calls.append(1)
                    return {'status': status, 'raw_photo': ref, 'secret': 'must-not-leak'}
                result = run_bot_pair(session, compare, logs.append)
                self.assertEqual(result, {'schema_version': 'photo-bot-result/1', 'status': status})
                self.assertEqual(calls, [1])
                self.assertEqual(session.deleted, 1)
                self.assertEqual(logs, [])

    def test_malformed_comparison_is_unavailable(self):
        for reply in (None, [], 'MATCH', {}, {'status': []}, {'status': 1}, {'status': 'ASYMMETRIC_PASS'}):
            session = Session()
            compare = Mock(return_value=reply)
            self.assertEqual(run_bot_pair(session, compare, None)['status'], 'UNAVAILABLE')
            compare.assert_called_once()
            self.assertEqual(session.deleted, 1)

    def test_failed_acquisition_also_cleans(self):
        session = Session()
        session.photos = Mock(side_effect=OSError('private bytes'))
        compare = Mock()
        self.assertEqual(run_bot_pair(session, compare, None)['status'], 'UNAVAILABLE')
        compare.assert_not_called()
        self.assertEqual(session.deleted, 1)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.sessions, self.order = [], []
        def factory(ref_id, candidate_id):
            self.order.append((ref_id, candidate_id))
            session = Session()
            self.sessions.append(session)
            return session
        self.compare = Mock(return_value={'status': 'MATCH'})
        self.now = 0
        self.checks = PhotoChecks(factory, self.compare, clock=lambda: self.now, max_pending=2)

    def test_interleaved_users_keep_photo_roles_and_release_state(self):
        self.checks.handle(event('generated-a', 1))
        self.checks.handle(event('real-b', 2, 'real'))
        self.assertEqual(self.checks.handle(event('real-a', 1)), [MESSAGES['MATCH']])
        self.assertEqual(self.checks.handle(event('generated-b', 2)), [MESSAGES['MATCH']])
        self.assertEqual(self.order, [('real-a', 'generated-a'), ('real-b', 'generated-b')])
        self.assertEqual(self.compare.call_count, 2)
        self.assertEqual([s.deleted for s in self.sessions], [1, 1])
        self.assertEqual(self.checks.pending, {})

    def test_cancel_expiry_and_pending_cap(self):
        self.checks.handle(event('one', 1))
        self.checks.handle(event('two', 2))
        self.assertEqual(self.checks.handle(event('three', 3)), [MESSAGES['busy']])
        self.assertEqual(len(self.checks.pending), 2)
        self.checks.handle(event(None, 1, kind='command', command='cancel'))
        self.assertNotIn((1, 1), self.checks.pending)
        self.now = 1801
        replies = self.checks.handle(event('fresh', 2))
        self.assertEqual(replies, [MESSAGES['expired'], MESSAGES['ask_real']])
        self.compare.assert_not_called()

    def test_creation_failure_has_no_private_reply(self):
        self.checks.session_factory = Mock(side_effect=RuntimeError('secret-photo'))
        self.checks.handle(event('generated'))
        self.assertEqual(self.checks.handle(event('real')), [MESSAGES['UNAVAILABLE']])
        self.compare.assert_not_called()
        self.assertEqual(self.checks.pending, {})

    def test_bot_runs_real_handler_with_injected_boundary_and_cleans_before_send(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'unit-test-placeholder'}):
            bot = importlib.import_module('app.bot')
        def update(fid, chat_type='private'):
            return {'message': {'chat': {'id': 1, 'type': chat_type}, 'from': {'id': 1},
                                'photo': [{'file_id': fid, 'file_size': 10}]}}
        sent = []
        def send(method, params):
            sent.append(params['text'])
            if params['text'] == MESSAGES['MATCH']:
                self.assertEqual(self.sessions[0].deleted, 1)
                self.assertIsNone(self.sessions[0].reference)
        with patch.object(bot, 'call', side_effect=send):
            bot.handle(update('ignored', 'group'), photo_checks=self.checks)
            bot.handle(update('generated'), photo_checks=self.checks)
            bot.handle(update('real'), photo_checks=self.checks)
        self.assertEqual(sent, [MESSAGES['ask_real'], MESSAGES['MATCH']])
        self.assertEqual(self.order, [('real', 'generated')])
        self.compare.assert_called_once()

    def test_bot_suppresses_verdict_and_raw_error_on_cleanup_failure(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'unit-test-placeholder'}):
            bot = importlib.import_module('app.bot')
        session = Session()
        session.delete_all = Mock(side_effect=RuntimeError('secret-photo-cleanup'))
        self.checks.session_factory = Mock(return_value=session)
        self.checks.handle(event('generated'))
        update = {'message': {'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1},
                              'photo': [{'file_id': 'real', 'file_size': 10}]}}
        with patch.object(bot, 'call') as send, self.assertLogs('bot', level='WARNING') as logs:
            bot.handle(update, photo_checks=self.checks)
        send.assert_not_called()
        session.delete_all.assert_called_once()
        self.assertNotIn('secret-photo', repr(logs.output))

    def test_bot_default_remains_disabled(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'unit-test-placeholder'}):
            bot = importlib.import_module('app.bot')
        update = {'message': {'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1},
                              'photo': [{'file_id': 'real', 'file_size': 10}]}}
        with patch.object(bot, 'call') as send:
            bot.handle(update)
        self.assertEqual(send.call_args.args[1]['text'], bot.TEXTS['ru']['fallback'])
        self.compare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
