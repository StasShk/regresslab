"""Warmup and execution-budget contracts; network I/O is mocked."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from regresslab.core import http_runner
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import calculate_metrics
from regresslab.core.models import Measurement
from regresslab.core.saved_results import (
    new_result_file,
    save_result,
    validate_experiment_compatibility,
)


def spec(**kwargs: object) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "name": "api",
            "url": "http://localhost:8000/test",
            "requests": 2,
            "concurrency": 1,
            **kwargs,
        }
    )


def install_client(monkeypatch: pytest.MonkeyPatch, handler: httpx.AsyncBaseTransport) -> None:
    real_client = httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=handler, **kwargs)

    monkeypatch.setattr(http_runner.httpx, "AsyncClient", client)


def test_defaults_do_not_change_old_execution() -> None:
    result = spec()
    assert result.warmup_requests == 0
    assert result.max_duration_seconds is None
    assert result.max_requests is None
    assert result.max_concurrency is None
    assert result.max_result_bytes is None
    assert result.timeout_seconds == 5.0


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("warmup_requests", -1),
        ("warmup_requests", 1.0),
        ("warmup_requests", True),
        ("max_duration_seconds", 0),
        ("max_duration_seconds", -0.5),
        ("max_duration_seconds", float("nan")),
        ("max_duration_seconds", float("inf")),
        ("max_duration_seconds", True),
        ("max_requests", 0),
        ("max_requests", 1.0),
        ("max_requests", True),
        ("max_concurrency", 0),
        ("max_concurrency", False),
        ("max_result_bytes", 0),
        ("max_result_bytes", -1),
        ("max_result_bytes", "1000"),
    ],
)
def test_invalid_budget_rejected(key: str, value: object) -> None:
    with pytest.raises(ValidationError):
        spec(**{key: value})


def test_preflight_request_cap_includes_warmup() -> None:
    assert spec(requests=2, warmup_requests=3, max_requests=5).max_requests == 5
    with pytest.raises(ValidationError, match="exceeds max_requests"):
        spec(requests=2, warmup_requests=3, max_requests=4)


def test_preflight_concurrency_cap() -> None:
    assert spec(concurrency=3, max_concurrency=3).concurrency == 3
    with pytest.raises(ValidationError, match="exceeds max_concurrency"):
        spec(concurrency=3, max_concurrency=2)


def test_warmup_uses_same_get_profile_and_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    codes = iter([500, 204, 200, 404, 201])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(next(codes))

    install_client(monkeypatch, httpx.MockTransport(handler))
    result = asyncio.run(http_runner.run_experiment(spec(requests=3, warmup_requests=2)))
    assert len(requests) == 5
    assert all(req.method == "GET" and str(req.url) == str(spec().url) for req in requests)
    assert result.request_count == 3
    assert result.successful_requests == 2
    assert result.failed_requests == 1
    assert len(result.latencies_ms) == 2
    assert "HTTP 404" in (result.failure_detail or "")
    assert calculate_metrics(result).error_rate == pytest.approx(1 / 3)


def test_warmup_time_is_excluded_from_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    install_client(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(200)))
    # Two warmup requests, measurement start, two measured requests, measurement end.
    stamps = iter([1.0, 2.0, 3.0, 4.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    monkeypatch.setattr(http_runner, "perf_counter", lambda: next(stamps))
    result = asyncio.run(http_runner.run_experiment(spec(warmup_requests=2)))
    assert result.latencies_ms == pytest.approx((1000.0, 1000.0))
    assert result.elapsed_seconds == 5.0
    assert result.successful_throughput_rps == pytest.approx(2 / 5)


def test_warmup_and_measurement_respect_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        active = 0
        peak = 0
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, peak, calls
            calls += 1
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.001)
                return httpx.Response(200)
            finally:
                active -= 1

        install_client(monkeypatch, httpx.MockTransport(handler))
        result = await http_runner.run_experiment(
            spec(requests=5, warmup_requests=5, concurrency=2)
        )
        assert calls == 10
        assert peak == 2
        assert active == 0
        assert result.request_count == 5

    asyncio.run(asyncio.wait_for(scenario(), timeout=2))


@pytest.mark.parametrize("block_warmup", [True, False])
def test_total_duration_cancels_inflight_http_and_does_not_return_partial_measurement(
    monkeypatch: pytest.MonkeyPatch, block_warmup: bool
) -> None:
    async def scenario() -> None:
        active = 0
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, calls
            calls += 1
            if block_warmup or calls > 1:
                active += 1
                try:
                    await asyncio.Event().wait()
                finally:
                    active -= 1
            return httpx.Response(200)

        install_client(monkeypatch, httpx.MockTransport(handler))
        test_spec = spec(
            warmup_requests=1,
            requests=1,
            max_duration_seconds=0.025,
            timeout_seconds=10,
        )
        with pytest.raises(http_runner.BenchmarkDeadlineExceeded, match="max_duration_seconds"):
            await http_runner.run_experiment(test_spec)
        assert active == 0
        assert calls == (1 if block_warmup else 2)

    asyncio.run(asyncio.wait_for(scenario(), timeout=2))


def test_no_deadline_keeps_existing_request_timeout_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.extensions["timeout"])
        return httpx.Response(200)

    install_client(monkeypatch, httpx.MockTransport(handler))
    result = asyncio.run(http_runner.run_experiment(spec(timeout_seconds=2.5, warmup_requests=1)))
    assert result.request_count == 2
    assert observed == [dict.fromkeys(("connect", "read", "write", "pool"), 2.5)] * 3


def test_saved_result_byte_cap_is_exact_and_no_partial_file(tmp_path: Path) -> None:
    measured = Measurement(
        latencies_ms=(1.0, 2.0),
        request_count=2,
        successful_requests=2,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    base = spec(name="café")
    result = calculate_metrics(measured)
    normal_path = tmp_path / "normal.json"
    save_result(normal_path, base, measured, result)
    raw = normal_path.read_bytes()
    # The serialized cap is itself part of the JSON; converge on an exact-byte limit.
    probe = tmp_path / "probe.json"
    serialized_size = 10**9
    for _ in range(10):
        save_result(probe, spec(name="café", max_result_bytes=serialized_size), measured, result)
        actual = probe.stat().st_size
        if actual == serialized_size:
            break
        serialized_size = actual
    assert actual == serialized_size
    probe.unlink()
    exact = spec(name="café", max_result_bytes=serialized_size)
    capped_path = tmp_path / "capped.json"
    save_result(capped_path, exact, measured, result)
    assert capped_path.stat().st_size == serialized_size
    assert json.loads(capped_path.read_text())["experiment"]["name"] == "café"
    too_small = spec(name="café", max_result_bytes=serialized_size - 1)
    target = tmp_path / "final.json"
    with (
        pytest.raises(ValueError, match="max_result_bytes") as _,
        new_result_file(target) as temporary,
    ):
        save_result(temporary, too_small, measured, result)
    assert not target.exists()
    assert not list(tmp_path.glob(".regresslab-*"))
    assert raw  # Existing uncapped saves remain supported.


@pytest.mark.parametrize("field", ["warmup_requests", "max_duration_seconds"])
def test_workload_compatibility_requires_same_warmup_and_deadline(field: str) -> None:
    before = spec()
    after = spec(**{field: 2 if field == "warmup_requests" else 2.0})
    with pytest.raises(ValueError, match=f"{field} differs"):
        validate_experiment_compatibility(before, after)


def test_optional_safety_caps_do_not_affect_comparability() -> None:
    validate_experiment_compatibility(
        spec(),
        spec(max_requests=20, max_concurrency=4, max_result_bytes=1000),
    )
