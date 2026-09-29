"""Public T3 contract entry point; bot implementation ships inside app/."""
from app.photo_compare import ProviderUnavailable, run_bot_pair


def call_provider(request, transport, environ, logger):
    """T3b is a separate, unimplemented task; do not call a real provider."""
    raise NotImplementedError('T3B_NOT_IMPLEMENTED')


def summarize_blind_eval(rows):
    """T3c is a separate, unimplemented task; no evaluation claim here."""
    raise NotImplementedError('T3C_NOT_IMPLEMENTED')
