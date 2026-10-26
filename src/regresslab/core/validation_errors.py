"""Safe CLI descriptions of validation errors without untrusted input values.

Pydantic errors may echo input_value, field names, arbitrary context and URLs.
Only allowlisted field labels, error kinds and fixed text may be printed.
"""

from pydantic import ValidationError
from yaml import YAMLError

_SAFE_FIELDS = frozenset(
    {
        "name",
        "url",
        "requests",
        "concurrency",
        "timeout_seconds",
        "warmup_requests",
        "bearer_token_env",
        "max_duration_seconds",
        "max_requests",
        "max_concurrency",
        "max_result_bytes",
        "environment",
        "git_sha",
        "service_revision",
        "runner_os",
        "runner_arch",
        "python_version",
        "environment_id",
        "config_fingerprint",
        "regresslab_version",
        "metrics",
        "measurement",
        "latencies_ms",
        "request_count",
        "successful_requests",
        "failed_requests",
        "elapsed_seconds",
        "failure_detail",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "successful_throughput_rps",
        "error_rate",
        "schema_version",
        "pairs",
        "id",
        "baseline",
        "candidate",
    }
)
_SAFE_RULE_MESSAGES = frozenset(
    {
        "pair IDs must be unique",
        "bearer_token_env cannot be combined with URL credentials",
        "requests + warmup_requests exceeds max_requests",
        "concurrency exceeds max_concurrency",
        # Exact messages emitted by Measurement's model-level invariants.
        # These contain fixed field names, never submitted values.
        "request_count must equal successful_requests + failed_requests",
        "latencies_ms must contain one entry per successful request",
        "measurement with requests requires elapsed_seconds > 0",
    }
)
_SAFE_ERROR_MESSAGES = {
    "missing": "Field required",
    "extra_forbidden": "Extra inputs are not permitted",
    "too_short": "at least 1 item required",
    "url_parsing": "invalid URL",
    "url_scheme": "invalid URL scheme",
    "greater_than": "must be greater than the allowed minimum",
    "greater_than_equal": "must meet the allowed minimum",
    "less_than_equal": "exceeds the allowed maximum",
    "string_pattern_mismatch": "invalid format",
    "string_too_short": "text too short",
    "string_too_long": "text too long",
    "int_type": "invalid integer",
    "float_type": "invalid number",
    "bool_type": "invalid boolean",
    "list_type": "invalid list",
    "tuple_type": "invalid tuple",
}


def safe_validation_details(error: ValidationError) -> str:
    """Describe field and category without Pydantic repr(input) or raw messages."""
    parts: list[str] = []
    for issue in error.errors(include_input=False, include_context=False, include_url=False)[:5]:
        components: list[str] = []
        for item in issue.get("loc", ()):
            if isinstance(item, str):
                components.append(item if item in _SAFE_FIELDS else "[unknown field]")
            elif isinstance(item, int):
                components.append("[index]")
        location = ".".join(components) if components else "model"
        kind = issue.get("type")
        description = _SAFE_ERROR_MESSAGES.get(kind, "invalid value")
        if kind == "value_error":
            message = issue.get("msg", "")
            # Exact-match only. All other custom validators may include secrets.
            custom = message.removeprefix("Value error, ")
            if custom in _SAFE_RULE_MESSAGES:
                description = custom
        parts.append(f"{location}: {description}")
    return "; ".join(parts) if parts else "invalid value"


def safe_cli_exception(error: Exception) -> str:
    """Avoid echoing Pydantic inputs or YAML source snippets in CLI output."""
    if isinstance(error, ValidationError):
        return f"invalid experiment: {safe_validation_details(error)}"
    if isinstance(error, YAMLError):
        # Only a known, fixed parser diagnosis is copied, never arbitrary excerpts.
        problem = getattr(error, "problem", None)
        if isinstance(problem, str) and problem.startswith("expected the node content"):
            return "invalid YAML: expected the node content"
        return "invalid YAML syntax"
    return str(error)
