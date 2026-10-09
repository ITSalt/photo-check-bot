"""Opt-in T3a bot wiring, with session creation and comparison injected by the host.

No provider is selected or called here. The default bot does not enable checks.
Texts come from app/bot_texts.json (ru and en); MESSAGES is the Russian set.

With a `gate` (app/check_gate.py) every complete pair runs in this order (D-11, D-12, D-7):
free-check counter allows -> spend cap reserves -> download (session_factory) ->
compare -> the counter is charged only on MATCH or MISMATCH; the reservation is
closed in a finally. A refusal downloads nothing and makes no provider call.
"""
import json
import pathlib
import time

from app.intake import pairing
from app.photo_compare import run_bot_pair
from app.verdict_notes import format_notes

TEXTS = json.loads((pathlib.Path(__file__).parent / "bot_texts.json").read_text(encoding="utf-8"))
KEYS = ('ask_generated', 'ask_real', 'expired', 'cancelled', 'unsupported', 'busy',
        'MATCH', 'MISMATCH', 'NEEDS_REVIEW', 'UNAVAILABLE', 'limit_reached', 'free_used_up', 'uncharged_limit',
        'welcome_checks', 'privacy', 'image_too_large', 'image_unreadable')
# Reasons a compare result may carry with UNAVAILABLE (app/vision_compare.py IMAGE_REASONS).
IMAGE_REASONS = ('image_too_large', 'image_unreadable')
MESSAGES = {key: TEXTS['ru'][key] for key in KEYS}
PRIVACY_COMMANDS = ('help', 'privacy')


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
                 gate=None):
        if not callable(session_factory) or not callable(compare):
            raise ValueError('INVALID_PHOTO_DEPENDENCY')
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError('INVALID_PENDING_LIMIT')
        self.session_factory, self.compare, self.logger = session_factory, compare, logger
        self.clock, self.max_pending = clock, max_pending
        self.gate = gate
        self.pending = {}

    def forget(self, user_id):
        """/forget: drop the user's half pair (file ids in memory) and their counter row.

        Raises when the counter row could not be deleted (the caller says so).
        """
        self.pending = {k: state for k, state in self.pending.items() if k[1] != user_id}
        if self.gate is not None:
            self.gate.forget(user_id)

    def tick(self):
        """Periodic housekeeping from the polling loop (the counter's daily purge)."""
        if self.gate is not None:
            self.gate.tick()

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
        # The privacy line goes with the first prompt to upload, before any photo is sent.
        replies = [text['welcome_checks'], text['privacy']] if command == 'start' else []
        for action in actions:
            if 'say' in action:
                replies.append(text[action['say']])
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
        try:
            try:
                session = self.session_factory(action['real'], action['generated'])
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
        finally:
            if ticket is not None:
                self.gate.settle(ticket, status)
        reply = text[status]
        if (status == 'UNAVAILABLE' and seen and isinstance(seen[0], dict)
                and seen[0].get('reason') in IMAGE_REASONS):
            # An image refused before any provider call: say why and what to send instead.
            return text[seen[0]['reason']]
        if seen and isinstance(seen[0], dict) and seen[0].get('status') == status:
            notes = format_notes(seen[0], lang)
            reply = reply + ' ' + notes if notes else reply
        return reply
