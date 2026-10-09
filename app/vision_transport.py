"""T4: HTTPS transport for an OpenAI-compatible chat-completions endpoint.

make_transport(base_url, timeout_cap, host_allowlist=...) returns
transport(request, headers, timeout_seconds) -> {"status": int, "body": dict | None},
the envelope the T3b boundary (call_provider) expects. urllib only.

Guarantees: HTTPS only; the URL host must be on the allowlist; no proxies from
the environment; no redirects (a 3xx is returned as a status, never followed, so
the Authorization header cannot travel to another host); the response read is
capped; TimeoutError propagates unchanged (T3b maps it). Failures that are not a
timeout come back as a non-200 status or a body of None; nothing here raises
text that could contain headers or response data.
"""
import json
import urllib.parse
import urllib.request

MAX_TIMEOUT = 120.0           # T3b accepts 0 < timeout <= 120
DEFAULT_RESPONSE_CAP = 1 << 20  # 1 MiB is far above any verdict answer
STATUS_NETWORK_ERROR = 599    # synthetic: connection failed, not a timeout


class TransportConfigError(ValueError):
    """Bad transport configuration. Messages never include the URL or secrets."""


def parse_allowlist(value):
    """'a.com, B.com' -> frozenset({'a.com','b.com'}); empty or malformed -> error."""
    hosts = frozenset(h.strip().lower() for h in str(value or "").split(",") if h.strip())
    if not hosts or any(not _valid_host(h) for h in hosts):
        raise TransportConfigError("host allowlist is empty or malformed")
    return hosts


def _valid_host(host):
    return all(c.isalnum() or c in ".-" for c in host) and not host.startswith((".", "-"))


def check_url(base_url, host_allowlist):
    """Return the normalised https URL or raise TransportConfigError."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise TransportConfigError("base url missing")
    parts = urllib.parse.urlsplit(base_url.strip())
    if parts.scheme != "https":
        raise TransportConfigError("base url must be https")
    if parts.username is not None or parts.password is not None:
        raise TransportConfigError("base url must not carry credentials")
    try:
        port = parts.port
    except ValueError:
        raise TransportConfigError("base url port invalid") from None
    if port not in (None, 443):
        raise TransportConfigError("base url port not allowed")
    host = (parts.hostname or "").lower()
    allowed = {h.lower() for h in host_allowlist}
    if not host or host not in allowed:
        raise TransportConfigError("base url host is not on the allowlist")
    if parts.fragment:
        raise TransportConfigError("base url must not have a fragment")
    return base_url.strip()


def _default_opener():
    """HTTPS-only opener built bare: no env proxies, no redirect handler, no http."""
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPSHandler())
    return opener


def make_transport(base_url, timeout_cap, *, host_allowlist, response_cap=DEFAULT_RESPONSE_CAP,
                   opener=None):
    """Build the transport. `opener` (tests): callable(urllib Request, timeout) -> response."""
    url = check_url(base_url, host_allowlist)
    if (not isinstance(timeout_cap, (int, float)) or isinstance(timeout_cap, bool)
            or not 0 < timeout_cap <= MAX_TIMEOUT):
        raise TransportConfigError("timeout cap must be in (0, 120]")
    if not isinstance(response_cap, int) or response_cap <= 0:
        raise TransportConfigError("response cap must be positive")
    if opener is None:
        real = _default_opener()
        opener = lambda req, timeout: real.open(req, timeout=timeout)  # noqa: E731

    def transport(request, headers, timeout_seconds):
        if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
                or timeout_seconds <= 0):
            raise TransportConfigError("timeout must be positive")
        timeout = min(float(timeout_seconds), float(timeout_cap))
        data = json.dumps(request, separators=(",", ":")).encode("utf-8")
        sent = {"Content-Type": "application/json", "Accept": "application/json"}
        sent.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=sent, method="POST")
        try:
            resp = opener(req, timeout)
        except TimeoutError:
            raise  # socket.timeout is TimeoutError
        except OSError as exc:  # URLError (incl. wrapped timeouts), SSL, refused, reset
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                raise TimeoutError("timeout") from None
            return {"status": STATUS_NETWORK_ERROR, "body": None}
        try:
            status = resp.status
            if status != 200:  # includes 3xx: redirects are refused, never followed
                return {"status": status, "body": None}
            raw = resp.read(response_cap + 1)
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()
        if len(raw) > response_cap:
            return {"status": 200, "body": None}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"status": 200, "body": None}
        return {"status": 200, "body": body if isinstance(body, dict) else None}

    return transport
