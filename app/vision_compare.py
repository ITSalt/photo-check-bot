"""T4 glue: the `compare(reference, candidate)` callable for run_bot_pair.

inventory call -> two order-swapped compare calls -> T2 core (app/vision_core.py),
each call through the real T3b adapter
core_t3.photo_compare_t3_candidate.call_provider (no other provider path). Enabled only by
env config; compare_from_env returns None when it is missing, and the bot then
keeps its current behaviour. Logs carry codes, model name and token/cost numbers
only: no bytes, hashes, base64, keys, identifiers or provider text.

Smoke test: python -m app.vision_compare REFERENCE_FILE CANDIDATE_FILE
"""
import logging
import re

from app.answer_rules import FIXES, line_fields
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
# WP-CONTROLS-04: a compare result without a valid candidate_view was read as IN_VIEW
# (app/vision_core.py _pop_views). A repair like the two in answer_rules.FIXES, counted per result.
VIEW_DEFAULTED = "VIEW_DEFAULTED"
# REVISE 2: repairs of an OUT_OF_VIEW result (app/vision_core.py _repair_out_of_view).
VIEW_VERDICT_FIXED = "VIEW_VERDICT_FIXED"
VIEW_ROI_FILLED = "VIEW_ROI_FILLED"
VIEW_FIXES = (VIEW_DEFAULTED, VIEW_VERDICT_FIXED, VIEW_ROI_FILLED)
ANSWER_FIXES = FIXES + VIEW_FIXES
_COUNTED_FIXES = ("ROI_CLAMPED",) + VIEW_FIXES


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


def _cost(body):
    usage = body.get("usage") if isinstance(body, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    return None if isinstance(cost, bool) or not isinstance(cost, (int, float)) else cost


OVERRUN = "SPEND_CAP_OVERRUN"
BEGIN = "SPEND_CAP_BEGIN"
# ImageRejected code -> the bot text key explaining it (app/bot_texts.json); refused
# before any provider call, so the check is UNAVAILABLE and not charged.
IMAGE_REASONS = {"SIDE_TOO_LONG": "image_too_large", "TOO_LARGE": "image_too_large",
                 "NO_SIZE": "image_unreadable"}


def _hook(hook, where):
    """Run a pre-send hook; True when the request may leave (no hook: True). A hook that
    returns False or raises stops the chain before the send (fail closed)."""
    if hook is None:
        return True
    try:
        return hook() is not False
    except Exception:
        log.error("spend_cap_error code=HOOK stage=%s", where)
        return False


def _report(on_call, cost):
    """Hand one call's cost (None: unknown) to the spend cap hook.

    The hook returns False to stop the check (its spend went past its hold); a hook
    that raises also stops it (fail closed). True when the chain may go on.
    """
    if on_call is None:
        return True
    try:
        return on_call(cost) is not False
    except Exception:
        log.error("spend_cap_error code=HOOK")
        return False


def make_compare(config, environ, call_provider, core, transport=None, on_call=None,
                 on_begin=None, on_not_sent=None, on_reject=None, on_codes=None):
    """compare(reference, candidate) -> the vision_core.assess dict, or {"status": "UNAVAILABLE"}.

    Never raises ProviderUnavailable: any provider failure (MISSING_KEY, TIMEOUT,
    HTTP_ERROR, MALFORMED_BODY) ends the chain with UNAVAILABLE, no further call.
    `call_provider` is the T3b adapter resolved by compare_from_env.

    `core` is the app.vision_core module (injected so a missing core_t2 disables
    the feature instead of breaking import).

    `on_call(cost)` (optional, the spend cap) runs after every provider call that was
    attempted: cost is the reply's usage.cost, or None when the call failed or the
    reply has no usable cost (the cap then counts its fallback). If it returns False
    the remaining calls are skipped and the check ends UNAVAILABLE (OVERRUN).

    `on_begin()` (optional, spend cap v2) runs before every provider call: it marks the
    request as leaving on the ledger. False (or raising) -> no send, the check ends
    UNAVAILABLE (BEGIN). `on_not_sent()` replaces on_call(None) only when call_provider
    failed before the transport was invoked (e.g. MISSING_KEY): only then is it proven
    that nothing left. Any failure after the transport was entered counts the fallback.

    `on_reject(stage, code, rule)` (optional, for the controls table) runs after a format
    reject whose rule is known; it gets fixed codes only and cannot change the check.

    Every completed check logs one `check_codes` line (WP-CONTROLS-01) and hands the code
    record to `on_codes(record)` (optional, the controls run); neither can change the check.
    """
    if transport is None:
        transport = make_transport(config["base_url"], config["timeout"],
                                   host_allowlist=config["allowlist"])
    model = config["model"]

    def ask(stage, request):
        if not _hook(on_begin, stage):
            raise ProviderUnavailable(BEGIN)     # nothing sent, nothing to record
        entered = []

        def counted(*args):
            entered.append(True)                 # from here on the request may have left
            return transport(*args)

        try:
            body = call_provider(request, counted, environ, _provider_sink)
        except BaseException:
            if entered or on_not_sent is None:
                _report(on_call, None)   # the call may still be billed: count the fallback
            else:
                _hook(on_not_sent, stage)   # provably never left
            raise
        _log_usage(model, stage, body)
        if not _report(on_call, _cost(body)):
            raise ProviderUnavailable(OVERRUN)   # no further call in this check
        return body

    def report_fixes(stage, fixes):
        """Log (and count) the repairs made to an accepted or rejected answer: codes and counts only."""
        for rule, n in fixes:
            if rule not in ANSWER_FIXES:
                continue
            if rule in _COUNTED_FIXES:
                log.info("vision_answer_fixed stage=%s rule=%s n=%d", stage, rule, int(n))
            else:
                log.info("vision_answer_fixed stage=%s rule=%s", stage, rule)
            if on_reject is not None:
                try:
                    on_reject(stage, "FIXED", rule)
                except Exception:
                    pass
        del fixes[:]

    def emit_codes(core, validated, answer_first, answer_second, result):
        """One log line and one callback per completed check; enum codes only, never fatal."""
        try:
            record = core.code_record(validated, answer_first, answer_second)
            record["a"] = result["status"]
            log.info("%s", core.codes_line(record))
        except Exception:
            log.error("check_codes_error code=RECORD")
            return
        if on_codes is not None:
            try:
                on_codes(record)
            except Exception:
                pass

    def compare(reference, candidate):
        unavailable = {"status": "UNAVAILABLE"}
        stage = "inventory"
        fixes = []
        try:
            data_url(candidate)  # reject a bad candidate before paying for call 1
            validated = core.parse_inventory(ask(stage, build_inventory_request(reference, model)), fixes)
            report_fixes(stage, fixes)
            first, second = core.build_compare_requests(validated, reference, candidate, model)
            stage = "compare_a"
            answer_first = core.parse_compare(ask(stage, first), validated, fixes)
            report_fixes(stage, fixes)
            stage = "compare_b"
            answer_second = core.parse_compare(ask(stage, second), validated, fixes)
            report_fixes(stage, fixes)
            stage = "assess"
            result = core.assess(validated, answer_first, answer_second)
            emit_codes(core, validated, answer_first, answer_second, result)
            return result
        except ImageRejected as exc:
            log.warning("vision_rejected code=%s", exc.args[0])
            reason = IMAGE_REASONS.get(exc.args[0])
            if reason:   # the seller gets one line saying what to do instead
                return {"status": "UNAVAILABLE", "reason": reason}
        except ProviderUnavailable as exc:
            code = str(exc)
            log.warning("vision_unavailable stage=%s code=%s", stage,
                        code if _CODE_RE.match(code) else "UNKNOWN")
        except AnswerError as exc:
            report_fixes(stage, fixes)
            log.warning("vision_bad_answer stage=%s code=%s%s", stage, exc.args[0], line_fields(exc))
            rule = getattr(exc, "rule", None)
            if rule is not None and on_reject is not None:
                try:
                    on_reject(stage, exc.args[0], rule)
                except Exception:   # a counter must never change the check
                    pass
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
    except ImportError:  # core_t2/ missing from the path: the feature stays off
        return None
    return vision_core


def compare_from_env(environ, *, transport=None, core=None, on_call=None, on_begin=None,
                     on_not_sent=None, on_reject=None, on_codes=None):
    """The compare callable, or None (not configured, or T3b / the T2 core unavailable).

    Every provider call goes through core_t3's call_provider; it is not injectable,
    so production cannot run on a double. `transport` is for tests and the smoke run.
    """
    config = load_config(environ)
    if config is None:
        return None
    call_provider = _default_call_provider()
    if call_provider is None:
        log.warning("vision disabled, call_provider unavailable")
        return None
    core = core or _default_core()
    if core is None:
        log.warning("vision disabled, T2 core unavailable")
        return None
    return make_compare(config, environ, call_provider, core, transport, on_call,
                        on_begin, on_not_sent, on_reject, on_codes)


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
