"""Focused tests for HTTP methods and JSON workloads."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from regresslab.core.config import ExperimentSpec
from regresslab.core.http_runner import run_experiment
from regresslab.core.provenance import configuration_fingerprint
from regresslab.core.saved_results import validate_experiment_compatibility


def spec(**kwargs: object) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {"name": "orders", "url": "http://localhost:8000/orders", "requests": 2, **kwargs}
    )


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_method_and_json_body_reach_server(method: str) -> None:
    from regresslab.core.http_runner import measure_http_request

    body = {"quantity": 2, "nested": {"active": True}}
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await measure_http_request(
                "http://example.test/orders",
                client=client,
                method=method,
                json_body=body if method != "GET" else None,
            )
        assert result.succeeded

    asyncio.run(run())
    assert len(received) == 1
    assert received[0].method == method
    if method == "GET":
        assert received[0].content == b""
    else:
        assert received[0].headers["content-type"] == "application/json"
        assert json.loads(received[0].content) == body


def test_run_experiment_sends_body_in_warmup_and_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"quantity": 2}
    seen: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, json.loads(request.content)))
        return httpx.Response(200)

    real_client = httpx.AsyncClient

    def make_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("regresslab.core.http_runner.httpx.AsyncClient", make_client)
    result = asyncio.run(run_experiment(spec(method="POST", json_body=body, warmup_requests=1)))
    assert result.request_count == 2
    assert result.successful_requests == 2
    assert seen == [("POST", body)] * 3


@pytest.mark.parametrize(
    ("changed", "field"),
    [
        ({"method": "PUT", "json_body": {"quantity": 2}}, "method"),
        ({"method": "POST", "json_body": {"quantity": 3}}, "json_body"),
    ],
)
def test_method_and_body_define_workload(changed: dict[str, object], field: str) -> None:
    baseline = spec(method="POST", json_body={"quantity": 2})
    candidate = spec(**changed)
    assert configuration_fingerprint(baseline) != configuration_fingerprint(candidate)
    with pytest.raises(ValueError, match=f"{field} differs"):
        validate_experiment_compatibility(baseline, candidate)


def test_same_request_on_different_hosts_is_comparable() -> None:
    baseline = spec(method="POST", json_body={"quantity": 2})
    candidate = spec(
        url="http://candidate.example:9000/orders", method="POST", json_body={"quantity": 2}
    )
    validate_experiment_compatibility(baseline, candidate)
    assert configuration_fingerprint(baseline) == configuration_fingerprint(candidate)


@pytest.mark.parametrize("method", ["get", "HEAD", "OPTIONS"])
def test_unsupported_method_is_rejected(method: str) -> None:
    with pytest.raises(ValidationError):
        spec(method=method)


@pytest.mark.parametrize("body", ["text", [1, 2], 123])
def test_json_body_requires_an_object(body: object) -> None:
    with pytest.raises(ValidationError):
        spec(method="POST", json_body=body)
