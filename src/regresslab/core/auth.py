"""Resolve explicitly named credentials without storing secrets in experiment models."""

import os
import re

from regresslab.core.config import ExperimentSpec


def bearer_headers(spec: ExperimentSpec) -> dict[str, str]:
    """Validate a Bearer token before network I/O; never echo its value in errors."""
    if spec.bearer_token_env is None:
        return {}
    token = os.environ.get(spec.bearer_token_env)
    if token is None:
        raise ValueError("bearer_token_env references an unset environment variable")
    # RFC 6750 b64token syntax also rejects whitespace, control bytes and Unicode.
    if re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token) is None:
        raise ValueError("bearer_token_env must resolve to a nonempty valid Bearer token")
    return {"Authorization": f"Bearer {token}"}
