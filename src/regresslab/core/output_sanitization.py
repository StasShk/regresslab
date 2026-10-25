"""Public, allowlisted diagnostic labels (never raw HTTPX error messages)."""

import re

# Exact names, not strings extracted from exceptions. These carry useful
# operational context without echoing URLs, proxy settings or auth details.
_ALLOWED_REQUEST_ERROR_NAMES = frozenset(
    {
        "RequestError",
        "TransportError",
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "NetworkError",
        "ConnectError",
        "ReadError",
        "WriteError",
        "CloseError",
        "ProtocolError",
        "LocalProtocolError",
        "RemoteProtocolError",
        "ProxyError",
        "UnsupportedProtocol",
        "DecodingError",
        "TooManyRedirects",
    }
)
_HTTP_STATUS = re.compile(r"HTTP [1-5][0-9]{2}\Z")


def safe_request_error_name(error: BaseException) -> str:
    """Return a known request-error class name without its untrusted message."""
    label = type(error).__name__
    return label if label in _ALLOWED_REQUEST_ERROR_NAMES else "RequestError"


def safe_failure_summary(failed_requests: int, detail: str | None) -> str | None:
    """Retain count and known reason only; replace all arbitrary text."""
    if failed_requests == 0:
        return None
    prefix = f"{failed_requests} requests failed; first error: "
    reason = detail[len(prefix) :] if detail is not None and detail.startswith(prefix) else ""
    if reason in _ALLOWED_REQUEST_ERROR_NAMES or reason == "redacted":
        return prefix + reason
    if _HTTP_STATUS.fullmatch(reason) is not None:
        return prefix + reason
    return prefix + "redacted"
