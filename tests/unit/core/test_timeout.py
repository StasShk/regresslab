"""Timeout validation and propagation to HTTP transport operations."""

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from regresslab.core import http_runner
from regresslab.core.config import ExperimentSpec


def make_spec(**overrides: object) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {"name": "api", "url": "http://localhost/health", "requests": 2, **overrides}
    )


def test_default_timeout_preserves_existing_behavior() -> None:
    assert make_spec().timeout_seconds == 5.0


@pytest.mark.parametrize("value", [0.1, 2, 10.0])
def test_accepts_positive_numeric_timeout(value: float) -> None:
    assert make_spec(timeout_seconds=value).timeout_seconds == value


@pytest.mark.parametrize(
    "value", [0, -1, float("inf"), float("-inf"), float("nan"), True, "5", None]
)
def test_rejects_invalid_timeout(value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        make_spec(timeout_seconds=value)
    assert exc.value.errors()[0]["loc"] == ("timeout_seconds",)
    assert exc.value.errors()[0]["type"] != "extra_forbidden"


@pytest.mark.parametrize("settings, expected", [({}, 5.0), ({"timeout_seconds": 0.25}, 0.25)])
def test_applies_timeout_to_each_request(
    monkeypatch: pytest.MonkeyPatch, settings: dict[str, float], expected: float
) -> None:
    observed: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.extensions["timeout"])
        return httpx.Response(200)

    # Preserve the real client's configuration path; replace only network I/O.
    real_client = httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(http_runner.httpx, "AsyncClient", client)
    result = asyncio.run(http_runner.run_experiment(make_spec(**settings)))

    assert result.successful_requests == 2
    assert observed == [dict.fromkeys(("connect", "read", "write", "pool"), expected)] * 2
