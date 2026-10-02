import asyncio
from collections.abc import Callable

import httpx
import pytest

from regresslab.core.http_runner import measure_http_get
from regresslab.core.models import Measurement, MeasurementStatus

URL = "http://localhost:8000/health"


def run_measurement(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Measurement:
    async def run() -> Measurement:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport,
            timeout=5.0,
        ) as client:
            return await measure_http_get(URL, client=client)

    return asyncio.run(run())


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([10.0, 10.25])
    monkeypatch.setattr(
        "regresslab.core.http_runner.perf_counter",
        lambda: next(times),
    )


@pytest.mark.parametrize("status_code", [200, 204])
def test_successful_request(fixed_clock, status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == URL
        return httpx.Response(status_code)

    result = run_measurement(handler)

    assert result.status is MeasurementStatus.SUCCESS
    assert result.request_count == 1
    assert result.successful_requests == 1
    assert result.failed_requests == 0
    assert result.latencies_ms == (250.0,)
    assert result.elapsed_seconds == 0.25
    assert result.throughput_rps == 4.0
    assert result.failure_detail is None


@pytest.mark.parametrize("status_code", [302, 404, 500])
def test_unsuccessful_http_status(fixed_clock, status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"Location": "/other"},
        )

    result = run_measurement(handler)

    assert result.status is MeasurementStatus.FAILED
    assert result.request_count == 1
    assert result.successful_requests == 0
    assert result.failed_requests == 1
    assert result.latencies_ms == ()
    assert result.elapsed_seconds == 0.25
    assert result.failure_detail == f"HTTP {status_code}"


@pytest.mark.parametrize(
    "error_type",
    [httpx.ReadTimeout, httpx.ConnectError],
)
def test_network_failure(
    fixed_clock,
    error_type: type[httpx.RequestError],
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("simulated failure", request=request)

    result = run_measurement(handler)

    assert result.status is MeasurementStatus.FAILED
    assert result.request_count == 1
    assert result.successful_requests == 0
    assert result.failed_requests == 1
    assert result.latencies_ms == ()
    assert result.elapsed_seconds == 0.25
    assert result.failure_detail is not None
    assert result.failure_detail.startswith(error_type.__name__)


def test_cancellation_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run_measurement(handler)
