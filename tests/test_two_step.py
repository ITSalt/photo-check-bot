"""WP-UX-01: 'step 1 of 2' replies and one reminder before a half pair expires (fake clock)."""
import os
import tempfile
import unittest
from unittest.mock import Mock

from app.intake import pairing
from app.photo_checks import PhotoChecks, TEXTS, reminder_from_env


def ev(fid, caption=None, user=1, **extra):
    return {'user_id': user, 'chat_id': user + 100, 'kind': 'photo', 'file_id': fid, 'caption': caption, **extra}


def cmd(name, user=1):
    return {'user_id': user, 'chat_id': user + 100, 'kind': 'command', 'command': name}


class Base(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.checks = PhotoChecks(Mock(), Mock(), clock=lambda: self.now, reminder_s=300)
        self.sent = []

    def send(self, chat, text):
        self.sent.append((chat, text))

    def tick(self, at):
        self.now = 1000.0 + at
        self.checks.tick(send=self.send)


class StepTextTests(Base):
    def test_first_photo_says_step_1_of_2(self):
        for lang in ('ru', 'en'):
            self.setUp()
            self.assertEqual(self.checks.handle(ev('g'), lang=lang), [TEXTS[lang]['ask_real']])
            self.assertIn('1', TEXTS[lang]['ask_real'])
            self.assertIn('2', TEXTS[lang]['ask_real'])
            self.assertIn('/privacy', TEXTS[lang]['ask_real'])

    def test_real_first_is_mirrored(self):
        for lang in ('ru', 'en'):
            self.setUp()
            self.assertEqual(self.checks.handle(ev('r', 'real'), lang=lang),
                             [TEXTS[lang]['ask_generated_after_real']])

    def test_start_prompt_is_step_1(self):
        out = self.checks.handle(cmd('start'), lang='en')
        self.assertEqual(out[-1], TEXTS['en']['ask_generated'])
        self.assertTrue(out[-1].startswith('Step 1 of 2'))
        out = self.checks.handle(cmd('cancel'), lang='ru')
        out = self.checks.handle(cmd('start'), lang='ru')
        self.assertTrue(out[-1].startswith('Шаг 1 из 2'))


class ReminderTests(Base):
    def test_no_reminder_before_threshold_then_exactly_one(self):
        self.checks.handle(ev('g'), lang='ru')
        self.tick(299)
        self.assertEqual(self.sent, [])
        self.tick(300)
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(chat, 101)
        self.assertEqual(text, TEXTS['ru']['reminder_real'].format(minutes=25))
        self.tick(330)
        self.tick(900)
        self.assertEqual(len(self.sent), 1)

    def test_minutes_left_and_language(self):
        self.checks.handle(ev('g'), lang='en')
        self.tick(600)
        self.assertEqual(self.sent[0][1], TEXTS['en']['reminder_real'].format(minutes=20))

    def test_mirrored_order_names_generated_photo(self):
        self.checks.handle(ev('r', 'real'), lang='en')
        self.tick(300)
        self.assertEqual(self.sent[0][1], TEXTS['en']['reminder_generated'].format(minutes=25))

    def test_completing_pair_before_threshold_no_reminder(self):
        self.checks.session_factory.side_effect = RuntimeError('x')
        self.checks.handle(ev('g'))
        self.now += 240
        self.checks.handle(ev('r', 'real'))
        self.assertEqual(self.checks.pending, {})
        self.tick(400)
        self.assertEqual(self.sent, [])

    def test_no_reminder_after_expiry(self):
        self.checks.handle(ev('g'))
        self.tick(pairing.EXPIRY_S + 1)
        self.assertEqual(self.sent, [])

    def test_cancel_and_forget_stop_reminders(self):
        self.checks.handle(ev('g'))
        self.checks.handle(cmd('cancel'))
        self.tick(400)
        self.checks.handle(ev('g2', user=2))
        self.checks.forget(2)
        self.tick(900)
        self.assertEqual(self.sent, [])

    def test_two_users_each_get_one(self):
        self.checks.handle(ev('a', user=1))
        self.checks.handle(ev('b', user=2))
        self.tick(300)
        self.tick(310)
        self.assertEqual(sorted(c for c, _ in self.sent), [101, 102])

    def test_unrelated_message_does_not_rearm(self):
        self.checks.handle(ev('g'))
        self.tick(300)
        self.checks.handle({'user_id': 1, 'chat_id': 101, 'kind': 'text', 'text': 'hi'})
        self.tick(400)
        self.assertEqual(len(self.sent), 1)

    def test_send_failure_is_not_retried_and_does_not_raise(self):
        self.checks.handle(ev('g'))
        boom = Mock(side_effect=OSError('net'))
        self.now += 300
        self.checks.tick(send=boom)
        self.checks.tick(send=boom)
        boom.assert_called_once()

    def test_tick_without_send_is_still_fine(self):
        self.checks.handle(ev('g'))
        self.now += 400
        self.checks.tick()
        self.tick(400)
        self.assertEqual(len(self.sent), 1)

    def test_logs_carry_no_ids(self):
        self.checks.handle(ev('secret-file-id', user=7777))
        with self.assertLogs('photo_checks', level='INFO') as cap:
            self.tick(300)
        self.assertEqual(cap.output, ['INFO:photo_checks:pair_reminder sent'])

    def test_no_files_written(self):
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                self.checks.handle(ev('g'))
                self.tick(300)
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(old)


class EnvTests(unittest.TestCase):
    def test_default_and_valid(self):
        self.assertEqual(reminder_from_env({}), 300)
        self.assertEqual(reminder_from_env({'PAIR_REMINDER_MIN': '1'}), 60)
        self.assertEqual(reminder_from_env({'PAIR_REMINDER_MIN': '25'}), 1500)

    def test_invalid_falls_back_with_code_only_log(self):
        for bad in ('0', '26', 'abc', '-3', '2.5'):
            with self.subTest(bad=bad), self.assertLogs('photo_checks', level='WARNING') as cap:
                self.assertEqual(reminder_from_env({'PAIR_REMINDER_MIN': bad}), 300)
            self.assertEqual(cap.output, ['WARNING:photo_checks:pair_reminder_config code=INVALID_MINUTES'])

    def test_constructor_reads_env(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'PAIR_REMINDER_MIN': '7'}):
            self.assertEqual(PhotoChecks(Mock(), Mock()).reminder_s, 420)


if __name__ == '__main__':
    unittest.main()
