"""T4 glue: the `compare(reference, candidate)` callable for run_bot_pair.

build_request -> call_provider (T3b, injected) -> parse_verdict. Enabled only by
env config; compare_from_env returns None when it is missing, and the bot then
keeps its current behaviour. Logs carry codes, model name and token/cost numbers
only: no bytes, hashes, base64, keys, identifiers or provider text.

Smoke test: python -m app.vision_compare REFERENCE_FILE CANDIDATE_FILE
"""
import logging
import re

from app.photo_compare import ProviderUnavailable
from app.vision_request import ImageRejected, build_request, parse_verdict
from app.vision_transport import (MAX_TIMEOUT, TransportConfigError, check_url,
                                  make_transport, parse_allowlist)

log = logging.getLogger("vision")

DEFAULT_TIMEOUT = 30.0
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")
_CODE_RE = re.compile(r"^[A-Z_]{1,32}$")
_REQUIRED = ("VISION_BASE_URL", "VISION_MODEL", "VISION_HOST_ALLOWLIST", "VISION_API_KEY")


class VisionConfigError(ValueError):
    """Config is present but invalid. Messages never include values."""


def load_config(environ):
    """Return {"base_url","model","allowlist","timeout"} or None when not configured.

    None: any required variable is unset or empty (a warning names the variables,
    never values). VisionConfigError: variables are set but invalid. The key itself
    is only checked for presence here; call_provider is its sole reader.
    """
    missing = [k for k in _REQUIRED if not str(environ.get(k) or "").strip()]
    if missing:
        if len(missing) < len(_REQUIRED):
            log.warning("vision disabled, unset: %s", ",".join(missing))
        return None
    model = environ["VISION_MODEL"].strip()
    if not _MODEL_RE.match(model):
        raise VisionConfigError("VISION_MODEL invalid")
    raw_timeout = str(environ.get("VISION_TIMEOUT") or "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
    except ValueError:
        raise VisionConfigError("VISION_TIMEOUT invalid") from None
    if not 0 < timeout <= MAX_TIMEOUT or timeout != timeout:
        raise VisionConfigError("VISION_TIMEOUT out of range")
    try:
        allowlist = parse_allowlist(environ["VISION_HOST_ALLOWLIST"])
        base_url = check_url(environ["VISION_BASE_URL"], allowlist)
    except TransportConfigError as exc:
        raise VisionConfigError(str(exc)) from None
    return {"base_url": base_url, "model": model, "allowlist": allowlist, "timeout": timeout}


def _provider_sink(event):
    """Logger handed to call_provider: forwards only short upper-case codes."""
    if isinstance(event, str) and _CODE_RE.match(event):
        log.info("provider_event code=%s", event)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return "n/a"
    return value


def _log_usage(model, body):
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    log.info("vision_call model=%s usage_prompt=%s usage_completion=%s cost=%s", model,
             _number(usage.get("prompt_tokens")), _number(usage.get("completion_tokens")),
             _number(usage.get("cost")))


def make_compare(config, environ, call_provider, transport=None):
    """compare(reference, candidate) -> {"status", "reasons"}; never raises ProviderUnavailable."""
    if transport is None:
        transport = make_transport(config["base_url"], config["timeout"],
                                   host_allowlist=config["allowlist"])
    model = config["model"]

    def compare(reference, candidate):
        try:
            request = build_request(reference, candidate, model)
        except ImageRejected as exc:
            log.warning("vision_rejected code=%s", exc.args[0])
            return {"status": "UNAVAILABLE", "reasons": []}
        try:
            body = call_provider(request, transport, environ, _provider_sink)
        except ProviderUnavailable as exc:
            code = str(exc)
            log.warning("vision_unavailable code=%s", code if _CODE_RE.match(code) else "UNKNOWN")
            return {"status": "UNAVAILABLE", "reasons": []}
        _log_usage(model, body)
        return parse_verdict(body)

    return compare


def _default_call_provider():
    try:
        from core_t3.photo_compare_t3_candidate import call_provider
    except ImportError:
        return None
    return call_provider


def compare_from_env(environ, *, call_provider=None, transport=None):
    """The compare callable, or None (not configured, or no T3b available)."""
    config = load_config(environ)
    if config is None:
        return None
    call_provider = call_provider or _default_call_provider()
    if call_provider is None:
        log.warning("vision disabled, call_provider unavailable")
        return None
    return make_compare(config, environ, call_provider, transport)


def _main(argv, environ):
    if len(argv) != 2:
        print("usage: python -m app.vision_compare REFERENCE_FILE CANDIDATE_FILE")
        return 2
    compare = compare_from_env(environ)
    if compare is None:
        print("vision not configured")
        return 2
    with open(argv[0], "rb") as f:
        reference = f.read()
    with open(argv[1], "rb") as f:
        candidate = f.read()
    result = compare(reference, candidate)
    print(result["status"], "; ".join(result["reasons"]))
    return 0 if result["status"] != "UNAVAILABLE" else 1


if __name__ == "__main__":
    import os
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    sys.exit(_main(sys.argv[1:], os.environ))
