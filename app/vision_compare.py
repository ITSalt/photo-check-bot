"""T4 glue: the `compare(reference, candidate)` callable for run_bot_pair.

inventory call -> two order-swapped compare calls -> T2 core (app/vision_core.py),
each call through call_provider (T3b, injected). Enabled only by
env config; compare_from_env returns None when it is missing, and the bot then
keeps its current behaviour. Logs carry codes, model name and token/cost numbers
only: no bytes, hashes, base64, keys, identifiers or provider text.

Smoke test: python -m app.vision_compare REFERENCE_FILE CANDIDATE_FILE
"""
import logging
import re

from app.photo_compare import ProviderUnavailable
from app.vision_request import (AnswerError, ImageRejected, build_inventory_request,
                                data_url)
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


def _log_usage(model, stage, body):
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    log.info("vision_call model=%s usage_prompt=%s usage_completion=%s cost=%s stage=%s", model,
             _number(usage.get("prompt_tokens")), _number(usage.get("completion_tokens")),
             _number(usage.get("cost")), stage)


# Unmeasured planning assumptions (tokens), see docs/t4-vision.md. The live smoke test
# replaces them with the numbers from the vision_call log lines.
ASSUMED = {"image": 1100, "inventory_prompt": 300, "inventory_out": 400,
           "compare_prompt": 900, "compare_out": 900}


def estimate_check_cost(prompt_usd_per_m, output_usd_per_m, assumed=ASSUMED):
    """Estimated {"prompt_tokens","completion_tokens","usd"} for one check (3 calls)."""
    prompt = (assumed["image"] + assumed["inventory_prompt"]
              + 2 * (2 * assumed["image"] + assumed["compare_prompt"]))
    out = assumed["inventory_out"] + 2 * assumed["compare_out"]
    usd = (prompt * prompt_usd_per_m + out * output_usd_per_m) / 1e6
    return {"prompt_tokens": prompt, "completion_tokens": out, "usd": usd}


def make_compare(config, environ, call_provider, core, transport=None):
    """compare(reference, candidate) -> the vision_core.assess dict, or {"status": "UNAVAILABLE"}.

    Never raises ProviderUnavailable.

    `core` is the app.vision_core module (injected so a missing core_t2 disables
    the feature instead of breaking import).
    """
    if transport is None:
        transport = make_transport(config["base_url"], config["timeout"],
                                   host_allowlist=config["allowlist"])
    model = config["model"]

    def ask(stage, request):
        body = call_provider(request, transport, environ, _provider_sink)
        _log_usage(model, stage, body)
        return body

    def compare(reference, candidate):
        unavailable = {"status": "UNAVAILABLE"}
        stage = "inventory"
        try:
            data_url(candidate)  # reject a bad candidate before paying for call 1
            validated = core.parse_inventory(ask(stage, build_inventory_request(reference, model)))
            first, second = core.build_compare_requests(validated, reference, candidate, model)
            stage = "compare_a"
            answer_first = core.parse_compare(ask(stage, first), validated)
            stage = "compare_b"
            answer_second = core.parse_compare(ask(stage, second), validated)
            stage = "assess"
            return core.assess(validated, answer_first, answer_second)
        except ImageRejected as exc:
            log.warning("vision_rejected code=%s", exc.args[0])
        except ProviderUnavailable as exc:
            code = str(exc)
            log.warning("vision_unavailable stage=%s code=%s", stage,
                        code if _CODE_RE.match(code) else "UNKNOWN")
        except AnswerError as exc:
            log.warning("vision_bad_answer stage=%s code=%s", stage, exc.args[0])
        return unavailable

    return compare


def _default_call_provider():
    try:
        from core_t3.photo_compare_t3_candidate import call_provider
    except ImportError:
        return None
    return call_provider


def _default_core():
    try:
        from app import vision_core
    except ImportError:  # core_t2/ is not in the current image (Dockerfile copies app/)
        return None
    return vision_core


def compare_from_env(environ, *, call_provider=None, transport=None, core=None):
    """The compare callable, or None (not configured, or T3b / the T2 core unavailable)."""
    config = load_config(environ)
    if config is None:
        return None
    call_provider = call_provider or _default_call_provider()
    if call_provider is None:
        log.warning("vision disabled, call_provider unavailable")
        return None
    core = core or _default_core()
    if core is None:
        log.warning("vision disabled, T2 core unavailable")
        return None
    return make_compare(config, environ, call_provider, core, transport)


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
    print(result["status"])
    return 0 if result["status"] != "UNAVAILABLE" else 1


if __name__ == "__main__":
    import os
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    sys.exit(_main(sys.argv[1:], os.environ))
