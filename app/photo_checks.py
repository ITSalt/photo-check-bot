"""Opt-in T3a bot wiring, with session creation and comparison injected by the host.

No provider is selected or called here. The default bot does not enable checks.
"""
import time

from app.intake import pairing
from app.photo_compare import run_bot_pair

MESSAGES = {
    'ask_generated': 'Пришлите сгенерированное фото товара.',
    'ask_real': 'Теперь пришлите фото настоящей вещи.',
    'expired': 'Время ожидания пары истекло. Пришлите фотографии заново.',
    'cancelled': 'Проверка отменена.',
    'unsupported': 'Пришлите фотографию или файл изображения.',
    'busy': 'Сейчас слишком много незавершённых пар. Попробуйте позже.',
    'MATCH': 'Проверенные детали совпали. Это не гарантия отсутствия других отличий.',
    'MISMATCH': 'Обнаружены отличия. Проверьте фото перед публикацией.',
    'NEEDS_REVIEW': 'Сходство не подтверждено. Нужна ручная проверка.',
    'UNAVAILABLE': 'Не удалось выполнить проверку. Попробуйте позже.',
}


class PhotoChecks:
    """Pair file IDs in memory; create a byte-owning session only for a complete pair.

    session_factory(reference_file_id, candidate_file_id) must clean up partial
    acquisition if it raises. On success its session owns cleanup via delete_all.
    compare(reference_bytes, candidate_bytes) is the injected comparison facade.
    """
    def __init__(self, session_factory, compare, logger=None, *, clock=time.monotonic, max_pending=1024):
        if not callable(session_factory) or not callable(compare):
            raise ValueError('INVALID_PHOTO_DEPENDENCY')
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError('INVALID_PENDING_LIMIT')
        self.session_factory, self.compare, self.logger = session_factory, compare, logger
        self.clock, self.max_pending = clock, max_pending
        self.pending = {}

    def handle(self, event):
        user, chat = event.get('user_id'), event.get('chat_id')
        if type(user) is not int or user <= 0 or type(chat) is not int or chat <= 0:
            return []
        now, key = self.clock(), (chat, user)
        # Bound retained file IDs even when the original sender never returns.
        previous = self.pending.get(key)
        self.pending = {k: state for k, state in self.pending.items()
                        if now - state['updated'] <= pairing.EXPIRY_S}
        state, actions = pairing.step(previous, event, now)
        self.pending.pop(key, None)
        if state['stage'] != 'idle':
            if len(self.pending) >= self.max_pending:
                return [MESSAGES['busy']]
            self.pending[key] = state
        replies = []
        for action in actions:
            if 'say' in action:
                replies.append(MESSAGES[action['say']])
            elif action.get('do') == 'check':
                try:
                    session = self.session_factory(action['real'], action['generated'])
                except Exception:
                    replies.append(MESSAGES['UNAVAILABLE'])
                    continue
                # Cleanup errors intentionally escape, so the bot publishes no verdict.
                result = run_bot_pair(session, self.compare, self.logger)
                replies.append(MESSAGES[result['status']])
        return replies
