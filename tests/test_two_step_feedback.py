"""WP-UX-02: seller marks a verdict as wrong -> a difference code, codes only (fake clock, fakes only)."""
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import lang
from app.photo_checks import FEEDBACK_CODES, PhotoChecks, Reply, TEXTS, parse_feedback_data
from tests.test_bot_wiring import FakeCompare, WiringBase, update

USER_ID, FILE_A, FILE_B = 777000111, 'marker-file-id-aaa', 'marker-file-id-bbb'
BOT_USER = 987654321


class Session:
    def photos(self):
        return b'r', b'g'

    def delete_all(self):
        pass


def ev(fid, caption=None, user=1):
    return {'user_id': user, 'chat_id': user + 100, 'kind': 'photo', 'file_id': fid, 'caption': caption}


def cmd_help():
    return {'user_id': 1, 'chat_id': 101, 'kind': 'command', 'command': 'help'}


def fb(token, code=None, user=1):
    return {'kind': 'feedback', 'user_id': user, 'chat_id': 1, 'token': token, 'code': code,
            'callback_id': 'cb'}


def token_of(reply):
    return reply.markup['inline_keyboard'][0][0]['callback_data'].split(':')[1]


class FeedbackBase(unittest.TestCase):
    status = 'MATCH'

    def setUp(self):
        self.now = 1000.0
        self.checks = PhotoChecks(lambda a, b: Session(), lambda a, b: {'status': self.status},
                                  clock=lambda: self.now, reminder_s=300)

    def verdict(self, user=1, lang='ru'):
        self.checks.handle(ev('g', user=user), lang=lang)
        out = self.checks.handle(ev('r', 'real', user=user), lang=lang)
        self.assertEqual(len(out), 1)
        return out[0]


class ButtonTests(FeedbackBase):
    def test_button_under_three_statuses_only(self):
        for status in ('MATCH', 'MISMATCH', 'NEEDS_REVIEW'):
            for lang in ('ru', 'en'):
                with self.subTest(status=status, lang=lang):
                    self.status = status
                    reply = self.verdict(lang=lang)
                    self.assertTrue(reply.startswith(TEXTS[lang][status]))
                    self.assertEqual(len(reply.markup['inline_keyboard']), 1)
                    (button,) = reply.markup['inline_keyboard'][0]
                    self.assertEqual(button['text'], TEXTS[lang]['feedback_button'])

    def test_no_button_on_unavailable_or_other_replies(self):
        self.status = 'UNAVAILABLE'
        self.assertIsNone(getattr(self.verdict(), 'markup', None))
        for text in self.checks.handle(ev('g'), lang='ru') + self.checks.handle(cmd_help(), lang='ru'):
            self.assertIsNone(getattr(text, 'markup', None))
        self.assertEqual(self.checks.verdicts, {})

    def test_no_button_on_gate_refusal(self):
        gate = Mock()
        gate.admit.return_value = (None, 'free_used_up')
        checks = PhotoChecks(lambda a, b: Session(), Mock(), gate=gate, clock=lambda: 1.0)
        checks.handle(ev('g'), lang='ru')
        out = checks.handle(ev('r', 'real'), lang='ru')
        self.assertEqual(out, [TEXTS['ru']['free_used_up']])
        self.assertIsNone(getattr(out[0], 'markup', None))
        self.assertEqual(checks.verdicts, {})

    def test_callback_data_is_short_and_opaque(self):
        self.checks.handle(ev(FILE_A, user=USER_ID), lang='en')
        reply = self.checks.handle(ev(FILE_B, 'real', user=USER_ID), lang='en')[0]
        datas = [reply.markup['inline_keyboard'][0][0]['callback_data']]
        asked = self.checks.handle_feedback(fb(token_of(reply), user=USER_ID), lang='en')[0]
        datas += [b['callback_data'] for row in asked.markup['inline_keyboard'] for b in row]
        self.assertEqual(len(datas), 6)
        for data in datas:
            self.assertLessEqual(len(data.encode()), 64)
            for marker in (str(USER_ID), str(USER_ID + 100), FILE_A, FILE_B, 'MATCH', 'match'):
                self.assertNotIn(marker, data)

    def test_five_codes_and_labels(self):
        reply = self.verdict(lang='en')
        asked = self.checks.handle_feedback(fb(token_of(reply)), lang='en')[0]
        self.assertEqual(asked, TEXTS['en']['feedback_ask'])
        buttons = [b for row in asked.markup['inline_keyboard'] for b in row]
        self.assertEqual([b['callback_data'].split(':')[2] for b in buttons],
                         ['color', 'size', 'logo', 'model', 'other'])
        self.assertEqual([b['text'] for b in buttons], [TEXTS['en']['code_' + c] for c in FEEDBACK_CODES])
        ru = self.checks.handle_feedback(fb(token_of(reply)), lang='ru')[0]
        self.assertEqual([b['text'] for row in ru.markup['inline_keyboard'] for b in row],
                         [TEXTS['ru']['code_' + c] for c in FEEDBACK_CODES])


class AcceptTests(FeedbackBase):
    def test_each_code_logs_one_line_once(self):
        for status in ('MATCH', 'MISMATCH', 'NEEDS_REVIEW'):
            for code in FEEDBACK_CODES:
                with self.subTest(status=status, code=code):
                    self.status = status
                    token = token_of(self.verdict())
                    with self.assertLogs('photo_checks', 'INFO') as logs:
                        out = self.checks.handle_feedback(fb(token, code), lang='ru')
                    self.assertEqual(out, [TEXTS['ru']['feedback_thanks']])
                    self.assertEqual(logs.output, [f'INFO:photo_checks:verdict_feedback a={status} diff={code}'])

    def test_second_press_refused_without_log(self):
        token = token_of(self.verdict())
        self.checks.handle_feedback(fb(token, 'size'), lang='en')
        for code in ('size', 'logo', None):
            with self.assertNoLogs('photo_checks', 'INFO'):
                out = self.checks.handle_feedback(fb(token, code), lang='en')
            self.assertEqual(out, [TEXTS['en']['feedback_refused']])

    def test_old_token_after_newer_verdict_refused(self):
        old = token_of(self.verdict())
        new = token_of(self.verdict())
        self.assertNotEqual(old, new)
        with self.assertNoLogs('photo_checks', 'INFO'):
            self.assertEqual(self.checks.handle_feedback(fb(old, 'color')), [TEXTS['ru']['feedback_refused']])
            self.assertEqual(self.checks.handle_feedback(fb(old)), [TEXTS['ru']['feedback_refused']])
        with self.assertLogs('photo_checks', 'INFO'):
            self.checks.handle_feedback(fb(new, 'color'))

    def test_other_users_token_refused(self):
        token = token_of(self.verdict(user=1))
        self.verdict(user=2)
        with self.assertNoLogs('photo_checks', 'INFO'):
            self.assertEqual(self.checks.handle_feedback(fb(token, 'color', user=2)),
                             [TEXTS['ru']['feedback_refused']])
            self.assertEqual(self.checks.handle_feedback(fb(token, 'color', user=3)),
                             [TEXTS['ru']['feedback_refused']])
        with self.assertLogs('photo_checks', 'INFO'):
            self.checks.handle_feedback(fb(token, 'color', user=1))

    def test_24_hours(self):
        token = token_of(self.verdict())
        self.now += 24 * 3600
        self.assertIsInstance(self.checks.handle_feedback(fb(token))[0], Reply)  # exactly 24 h still counts
        self.now += 1
        with self.assertNoLogs('photo_checks', 'INFO'):
            self.assertEqual(self.checks.handle_feedback(fb(token, 'color')), [TEXTS['ru']['feedback_refused']])
            self.assertEqual(self.checks.handle_feedback(fb(token)), [TEXTS['ru']['feedback_refused']])

    def test_unknown_code_or_token_refused(self):
        token = token_of(self.verdict())
        with self.assertNoLogs('photo_checks', 'INFO'):
            for bad in (fb(token, 'price'), fb('zzzz', 'color'), fb(None, 'color'), fb(token, user=None)):
                self.assertEqual(self.checks.handle_feedback(bad), [TEXTS['ru']['feedback_refused']])

    def test_forget_clears_entry(self):
        token = token_of(self.verdict())
        self.checks.forget(1)
        self.assertEqual(self.checks.verdicts, {})
        with self.assertNoLogs('photo_checks', 'INFO'):
            self.assertEqual(self.checks.handle_feedback(fb(token, 'color')), [TEXTS['ru']['feedback_refused']])

    def test_memory_is_bounded_and_pruned(self):
        checks = PhotoChecks(lambda a, b: Session(), lambda a, b: {'status': 'MATCH'},
                             clock=lambda: self.now, max_pending=2)
        for user in (1, 2, 3):
            checks.handle(ev('g', user=user))
            checks.handle(ev('r', 'real', user=user))
            self.now += 1
        self.assertEqual(sorted(checks.verdicts), [2, 3])
        self.now += 24 * 3600
        checks.handle(ev('g', user=4))
        checks.handle(ev('r', 'real', user=4))
        self.assertEqual(list(checks.verdicts), [4])

    def test_log_line_carries_no_ids(self):
        self.checks.handle(ev(FILE_A, user=USER_ID))
        token = token_of(self.checks.handle(ev(FILE_B, 'real', user=USER_ID))[0])
        with self.assertLogs('photo_checks', 'INFO') as logs:
            self.checks.handle_feedback(fb(token, 'logo', user=USER_ID))
        text = '\n'.join(logs.output)
        for marker in (str(USER_ID), str(USER_ID + 100), FILE_A, FILE_B, token):
            self.assertNotIn(marker, text)


class ParseDataTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_feedback_data('fb:abcd1234'), ('abcd1234', None))
        self.assertEqual(parse_feedback_data('fb:abcd1234:logo'), ('abcd1234', 'logo'))
        for bad in (None, '', 'fb', 'fb:', 'x:abcd', 'fb:a:b:c', 'fb:abcd:price', 5):
            self.assertIsNone(parse_feedback_data(bad), bad)


def callback(user, data, lang='ru', chat_type='private', chat=None, cid='cbid-1'):
    return {'update_id': 2, 'callback_query': {
        'id': cid, 'from': {'id': user, 'language_code': lang}, 'data': data,
        'message': {'message_id': 5, 'chat': {'id': user if chat is None else chat, 'type': chat_type}}}}


class BotTests(WiringBase):
    """Through app.bot.handle with the real wiring: keyboard goes out, every callback is answered."""

    def run_all(self, checks, *updates):
        calls = []
        with patch.object(self.bot, 'call', side_effect=lambda m, p=None, **k: calls.append((m, p))):
            for u in updates:
                self.bot.handle(u, photo_checks=checks)
        return calls

    def checked(self, lang='ru'):
        checks = self.build(FakeCompare(self, {'status': 'MISMATCH'}))
        calls = self.run_all(checks, update(BOT_USER, lang, photo='gen-file-id-1'),
                             update(BOT_USER, lang, photo='real-file-id-1'))
        return checks, calls

    @staticmethod
    def button_data(calls):
        return json.loads(calls[-1][1]['reply_markup'])['inline_keyboard'][0][0]['callback_data']

    def test_end_to_end(self):
        checks, calls = self.checked('en')
        self.assertEqual(calls[-1][0], 'sendMessage')
        self.assertNotIn('reply_markup', calls[0][1])
        data = self.button_data(calls)
        calls = self.run_all(checks, callback(BOT_USER, data, 'en'))
        self.assertEqual(calls[0], ('answerCallbackQuery', {'callback_query_id': 'cbid-1'}))
        self.assertEqual(calls[1][1]['text'], TEXTS['en']['feedback_ask'])
        code = self.button_data(calls)
        with self.assertLogs('photo_checks', 'INFO') as logs:
            calls = self.run_all(checks, callback(BOT_USER, code, 'en', cid='cbid-2'))
        self.assertEqual(logs.output, ['INFO:photo_checks:verdict_feedback a=MISMATCH diff=color'])
        self.assertEqual([c[0] for c in calls], ['answerCallbackQuery', 'sendMessage'])
        self.assertEqual(calls[1][1]['text'], TEXTS['en']['feedback_thanks'])
        with self.assertNoLogs('photo_checks', 'INFO'):
            calls = self.run_all(checks, callback(BOT_USER, code, 'en', cid='cbid-3'))
        self.assertEqual(calls[0], ('answerCallbackQuery', {'callback_query_id': 'cbid-3'}))
        self.assertEqual(calls[1][1]['text'], TEXTS['en']['feedback_refused'])

    def test_every_callback_is_answered(self):
        checks, _ = self.checked()
        for upd in (callback(BOT_USER, 'fb:nope'), callback(BOT_USER, 'other-data'),
                    callback(5, 'fb:abcd:color', chat_type='group', chat=-5), callback(5, None)):
            for pc in (checks, None):
                with self.subTest(update=upd, checks=pc is not None):
                    calls = self.run_all(pc, upd)
                    self.assertEqual(calls[0], ('answerCallbackQuery', {'callback_query_id': 'cbid-1'}))
                    self.assertEqual([c[0] for c in calls].count('answerCallbackQuery'), 1)
        calls = self.run_all(checks, callback(BOT_USER, 'fb:nope'))
        self.assertEqual([c[0] for c in calls], ['answerCallbackQuery', 'sendMessage'])

    def test_answer_failure_does_not_stop_the_reply(self):
        checks, _ = self.checked()
        sent = []

        def call(method, params=None, **k):
            if method == 'answerCallbackQuery':
                raise RuntimeError('boom')
            sent.append(method)
        with patch.object(self.bot, 'call', side_effect=call):
            self.bot.handle(callback(BOT_USER, 'fb:nope'), photo_checks=checks)
        self.assertEqual(sent, ['sendMessage'])

    def test_group_callback_gets_no_feedback_reply(self):
        checks, calls = self.checked()
        data = self.button_data(calls)
        calls = self.run_all(checks, callback(BOT_USER, data, chat_type='group', chat=-100))
        self.assertEqual([c[0] for c in calls], ['answerCallbackQuery'])
        self.assertEqual(self.bot.parse_feedback(callback(1, 'fb:ab:size')),
                         {'kind': 'feedback', 'user_id': 1, 'chat_id': 1, 'token': 'ab', 'code': 'size',
                          'callback_id': 'cbid-1'})
        self.assertIsNone(self.bot.parse_feedback(callback(1, 'fb:ab:size', chat_type='supergroup')))

    def test_forget_via_bot_clears_feedback(self):
        checks, calls = self.checked()
        data = self.button_data(calls)
        self.run_all(checks, update(BOT_USER, 'ru', text='/forget'))
        calls = self.run_all(checks, callback(BOT_USER, data + ':color'))
        self.assertEqual(calls[1][1]['text'], TEXTS['ru']['feedback_refused'])

    def test_polling_asks_for_callback_queries(self):
        seen = []

        def call(method, params=None, timeout=None):
            seen.append((method, dict(params or {})))
            raise RuntimeError('stop')
        with tempfile.TemporaryDirectory() as data_dir, \
                patch.dict(os.environ, {'DATA_DIR': data_dir}), \
                patch.object(self.bot, 'call', side_effect=call), patch.object(self.bot.waitlist, 'startup'), \
                patch.object(self.bot.waitlist, 'maybe_purge'), \
                patch.object(self.bot.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.bot.main()
        method, params = seen[0]
        self.assertEqual(method, 'getUpdates')
        self.assertEqual(json.loads(params['allowed_updates']), ['message', 'callback_query'])

    def test_plain_messages_unchanged(self):
        calls = self.run_all(None, update(BOT_USER, 'ru', text='/start'))
        # Same text as before; the only addition is the Русский / English keyboard (WP-UX-03).
        self.assertEqual(calls, [('sendMessage', {'chat_id': BOT_USER, 'text': TEXTS['ru']['welcome'],
                                                  'reply_markup': json.dumps(lang.keyboard())})])


class ParseUpdateUntouched(unittest.TestCase):
    def test_parse_update_still_ignores_callbacks_groups_unknown(self):
        from app.intake.parse import parse_update
        self.assertIsNone(parse_update(callback(1, 'fb:ab')))
        self.assertIsNone(parse_update({'update_id': 1, 'edited_message': {'chat': {'type': 'private', 'id': 1}}}))
        self.assertIsNone(parse_update({'message': {'chat': {'type': 'group', 'id': -1},
                                                    'from': {'id': 1}, 'text': 'hi'}}))


if __name__ == '__main__':
    unittest.main()
