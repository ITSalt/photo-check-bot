"""Opt-in T3a bot wiring, with session creation and comparison injected by the host.

No provider is selected or called here. The default bot does not enable checks.
Texts come from app/bot_texts.json (ru and en); MESSAGES is the Russian set.

With a `gate` (app/check_gate.py) every complete pair runs in this order (D-11, D-12, D-7):
free-check counter allows -> spend cap reserves -> download (session_factory) ->
compare -> the counter is charged only on MATCH or MISMATCH; the reservation is
closed in a finally. A refusal downloads nothing and makes no provider call.
"""
import json
import logging
import math
import os
import pathlib
import time

from app.intake import pairing
from app.intake.download import TooLarge
from app.photo_compare import run_bot_pair
from app.verdict_notes import format_notes

log = logging.getLogger('photo_checks')
TEXTS = json.loads((pathlib.Path(__file__).parent / "bot_texts.json").read_text(encoding="utf-8"))
KEYS = ('ask_generated', 'ask_real', 'expired', 'cancelled', 'unsupported', 'busy',
        'MATCH', 'MISMATCH', 'NEEDS_REVIEW', 'UNAVAILABLE', 'limit_reached', 'free_used_up', 'uncharged_limit',
        'welcome_checks', 'privacy', 'image_too_large', 'image_unreadable',
        'ask_generated_after_real', 'reminder_real', 'reminder_generated')
# Reasons a compare result may carry with UNAVAILABLE (app/vision_compare.py IMAGE_REASONS).
IMAGE_REASONS = ('image_too_large', 'image_unreadable')
MESSAGES = {key: TEXTS['ru'][key] for key in KEYS}
PRIVACY_COMMANDS = ('help', 'privacy')
REMINDER_ENV = 'PAIR_REMINDER_MIN'
REMINDER_DEFAULT_MIN = 5
REMINDER_MAX_MIN = 25


def reminder_from_env(environ):
    """Seconds before the one reminder (env PAIR_REMINDER_MIN, 1..25 minutes, default 5)."""
    raw = str(environ.get(REMINDER_ENV) or '').strip()
    if not raw:
        return REMINDER_DEFAULT_MIN * 60
    if raw.isascii() and raw.isdigit() and 1 <= int(raw) <= REMINDER_MAX_MIN:
        return int(raw) * 60
    log.warning('pair_reminder_config code=INVALID_MINUTES')
    return REMINDER_DEFAULT_MIN * 60


def messages(lang):
    texts = TEXTS.get(lang, TEXTS['ru'])
    return {key: texts[key] for key in KEYS}


class PhotoChecks:
    """Pair file IDs in memory; create a byte-owning session only for a complete pair.

    session_factory(reference_file_id, candidate_file_id) must clean up partial
    acquisition if it raises. On success its session owns cleanup via delete_all.
    compare(reference_bytes, candidate_bytes) is the injected comparison facade.
    gate (optional) admits a check (free-check counter, spend cap) and settles it.
    """
    def __init__(self, session_factory, compare, logger=None, *, clock=time.monotonic, max_pending=1024,
                 gate=None, reminder_s=None):
        if not callable(session_factory) or not callable(compare):
            raise ValueError('INVALID_PHOTO_DEPENDENCY')
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError('INVALID_PENDING_LIMIT')
        self.session_factory, self.compare, self.logger = session_factory, compare, logger
        self.clock, self.max_pending = clock, max_pending
        self.gate = gate
        self.pending = {}
        # In memory only: per half pair the age it was reminded at and the reply language.
        self.reminders = {}
        self.reminder_s = reminder_from_env(os.environ) if reminder_s is None else reminder_s

    def forget(self, user_id):
        """/forget: drop the user's half pair (file ids in memory) and their counter row.

        Raises when the counter row could not be deleted (the caller says so).
        """
        self.pending = {k: state for k, state in self.pending.items() if k[1] != user_id}
        self.reminders = {k: v for k, v in self.reminders.items() if k[1] != user_id}
        if self.gate is not None:
            self.gate.forget(user_id)

    def tick(self, send=None):
        """Periodic housekeeping from the polling loop (the counter's daily purge).

        With send(chat_id, text) it also sends one reminder per half pair that is older
        than reminder_s and not yet expired. Pairs live in memory: a restart loses reminders.
        """
        if self.gate is not None:
            self.gate.tick()
        if send is None:
            return
        now = self.clock()
        self.reminders = {k: v for k, v in self.reminders.items() if k in self.pending}
        for key, state in list(self.pending.items()):
            age = now - state['updated']
            if age < self.reminder_s or age >= pairing.EXPIRY_S:
                continue
            if self.reminders.get(key, {}).get('updated') == state['updated']:
                continue
            lang = self.reminders.get(key, {}).get('lang', 'ru')
            self.reminders[key] = {'updated': state['updated'], 'lang': lang, 'sent': True}
            missing = 'reminder_real' if state['stage'] == 'have_generated' else 'reminder_generated'
            minutes = max(1, math.ceil((pairing.EXPIRY_S - age) / 60))
            try:
                send(key[0], messages(lang)[missing].format(minutes=minutes))
                log.info('pair_reminder sent')
            except Exception:
                log.warning('pair_reminder_error code=SEND')

    def handle(self, event, lang='ru'):
        user, chat = event.get('user_id'), event.get('chat_id')
        if type(user) is not int or user <= 0 or type(chat) is not int or chat <= 0:
            return []
        text = messages(lang)
        command = event.get('command') if event.get('kind') == 'command' else None
        if command in PRIVACY_COMMANDS:
            # Help and privacy leave the pairing state as it is.
            return [text['welcome_checks'], text['privacy']] if command == 'help' else [text['privacy']]
        now, key = self.clock(), (chat, user)
        # Bound retained file IDs even when the original sender never returns.
        previous = self.pending.get(key)
        self.pending = {k: state for k, state in self.pending.items()
                        if now - state['updated'] <= pairing.EXPIRY_S}
        state, actions = pairing.step(previous, event, now)
        self.pending.pop(key, None)
        if state['stage'] != 'idle':
            if len(self.pending) >= self.max_pending:
                return [text['busy']]
            self.pending[key] = state
            self.reminders[key] = {**self.reminders.get(key, {}), 'lang': lang}
        else:
            self.reminders.pop(key, None)
        # The privacy line goes with the first prompt to upload, before any photo is sent.
        replies = [text['welcome_checks'], text['privacy']] if command == 'start' else []
        for action in actions:
            if 'say' in action:
                say = action['say']
                if say == 'ask_generated' and state['stage'] == 'have_real':
                    say = 'ask_generated_after_real'
                replies.append(text[say])
            elif action.get('do') == 'check':
                replies.append(self._check(user, action, text, lang))
        return replies

    def _check(self, user, action, text, lang):
        ticket = None
        if self.gate is not None:
            ticket, refusal = self.gate.admit(user)
            if ticket is None:
                # Nothing was downloaded and no provider is called.
                return text[refusal]
        status = None
        refused = False
        try:
            try:
                session = self.session_factory(action['real'], action['generated'])
            except TooLarge:
                # Declared or streamed over the byte cap: refused, not a failure of ours.
                refused = True
                return text['image_too_large']
            except Exception:
                return text['UNAVAILABLE']
            # Cleanup errors intentionally escape, so the bot publishes no verdict.
            # run_bot_pair keeps the exact T3a result; the notes ride on the side.
            seen = []
            def compare(reference, candidate):
                seen.append(self.compare(reference, candidate))
                return seen[-1]
            result = run_bot_pair(session, compare, self.logger)
            status = result['status']
            refused = (status == 'UNAVAILABLE' and bool(seen) and isinstance(seen[0], dict)
                       and seen[0].get('reason') in IMAGE_REASONS)
        finally:
            if ticket is not None:
                self.gate.settle(ticket, status, refused=refused)
        reply = text[status]
        if (status == 'UNAVAILABLE' and seen and isinstance(seen[0], dict)
                and seen[0].get('reason') in IMAGE_REASONS):
            # An image refused before any provider call: say why and what to send instead.
            return text[seen[0]['reason']]
        if seen and isinstance(seen[0], dict) and seen[0].get('status') == status:
            notes = format_notes(seen[0], lang)
            reply = reply + ' ' + notes if notes else reply
        return reply
