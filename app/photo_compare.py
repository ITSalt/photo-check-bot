"""T3a session boundary. No provider, network, filesystem or logging side effects."""


class ProviderUnavailable(Exception):
    """Comparison dependency could not provide a verdict."""


def run_bot_pair(session, compare, logger):
    """Consume one session, compare once, discard private fields, then clean up.

    The injected session owns all photo resources. Cleanup failure propagates;
    callers must not publish a verdict in that case. No exception is logged.
    """
    status = 'UNAVAILABLE'
    try:
        reference, candidate = session.photos()
        result = compare(reference, candidate)
        if isinstance(result, dict):
            value = result.get('status')
            if isinstance(value, str) and value in ('MATCH', 'MISMATCH', 'NEEDS_REVIEW', 'UNAVAILABLE'):
                status = value
    except Exception:
        # Includes acquisition, malformed result access, and unexpected comparison errors.
        status = 'UNAVAILABLE'
    finally:
        session.delete_all()
    return {'schema_version': 'photo-bot-result/1', 'status': status}

