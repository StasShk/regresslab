import asyncio

import httpx
import pytest

from regresslab.core import http_runner
from regresslab.core.config import ExperimentSpec
from regresslab.core.models import Measurement, MeasurementStatus


def make_spec(requests: int, concurrency: int) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "name": "test",
            "url": "http://localhost:8000/health",
            "requests": requests,
            "concurrency": concurrency,
        }
    )


def install_client(monkeypatch, handler):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        http_runner.httpx,
        "AsyncClient",
        lambda **kwargs: client,
    )
    return client


@pytest.mark.parametrize(
    ("statuses", "expected_successes"),
    [
        ([200, 204, 200], 3),
        ([200, 500, None], 1),
        ([500, None, 404], 0),
    ],
)
def test_aggregates_results(monkeypatch, statuses, expected_successes):
    responses = iter(statuses)

    def handler(request):
        status = next(responses)
        if status is None:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(status)

    client = install_client(monkeypatch, handler)
    result = asyncio.run(http_runner.run_experiment(make_spec(len(statuses), 2)))

    assert result.status is MeasurementStatus.SUCCESS
    assert result.request_count == len(statuses)
    assert result.successful_requests == expected_successes
    assert result.failed_requests == len(statuses) - expected_successes
    assert len(result.latencies_ms) == expected_successes
    assert result.elapsed_seconds > 0
    assert result.throughput_rps == pytest.approx(expected_successes / result.elapsed_seconds)
    assert (result.failure_detail is not None) == (result.failed_requests > 0)
    assert client.is_closed


@pytest.mark.parametrize(
    ("requests", "concurrency"),
    [(10, 3), (2, 5), (4, 1)],
)
def test_limits_concurrency(monkeypatch, requests, concurrency):
    async def scenario():
        active = 0
        peak = 0
        calls = 0
        expected_workers = min(requests, concurrency)
        workers_started = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            nonlocal active, peak, calls
            calls += 1
            active += 1
            peak = max(peak, active)

            if active == expected_workers:
                workers_started.set()

            try:
                await release.wait()
                return httpx.Response(200)
            finally:
                active -= 1

        client = install_client(monkeypatch, handler)
        task = asyncio.create_task(http_runner.run_experiment(make_spec(requests, concurrency)))

        await workers_started.wait()
        assert calls == expected_workers
        release.set()

        result = await task

        assert calls == requests
        assert peak == expected_workers
        assert active == 0
        assert result.request_count == requests
        assert client.is_closed

    asyncio.run(asyncio.wait_for(scenario(), timeout=3.0))


@pytest.mark.parametrize("cancel_all", [False, True])
def test_worker_cancellation_aborts_run(monkeypatch, cancel_all):
    async def scenario():
        active = 0
        calls = 0
        workers_started = asyncio.Event()
        block = asyncio.Event()

        async def handler(request):
            nonlocal active, calls
            calls += 1
            call_number = calls
            active += 1
            if active == 2:
                workers_started.set()

            try:
                await workers_started.wait()
                if cancel_all or call_number == 1:
                    raise asyncio.CancelledError
                await block.wait()
                return httpx.Response(200)
            finally:
                active -= 1

        client = install_client(monkeypatch, handler)

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(http_runner.run_experiment(make_spec(10, 2)), timeout=1.0)

        assert calls == 2
        assert active == 0
        assert client.is_closed

    asyncio.run(asyncio.wait_for(scenario(), timeout=3.0))


def test_uses_total_elapsed_time(monkeypatch):
    client = install_client(
        monkeypatch,
        lambda request: httpx.Response(200),
    )
    # Run start, request 1 start/end, request 2 start/end, run end.
    times = iter([10.0, 10.1, 10.2, 10.3, 10.4, 11.0])
    monkeypatch.setattr(
        http_runner,
        "perf_counter",
        lambda: next(times),
    )

    result = asyncio.run(http_runner.run_experiment(make_spec(2, 1)))

    assert result.latencies_ms == pytest.approx((100.0, 100.0))
    assert result.elapsed_seconds == 1.0
    assert result.throughput_rps == 2.0
    assert client.is_closed


def test_rejects_incomplete_results(monkeypatch):
    async def incomplete_measurement(url, *, client):
        return Measurement(
            latencies_ms=(),
            request_count=0,
            successful_requests=0,
            failed_requests=0,
            elapsed_seconds=1.0,
            status=MeasurementStatus.SUCCESS,
        )

    client = install_client(monkeypatch, lambda request: httpx.Response(200))
    monkeypatch.setattr(http_runner, "measure_http_get", incomplete_measurement)

    with pytest.raises(RuntimeError, match="completed 0 of 2 requests"):
        asyncio.run(http_runner.run_experiment(make_spec(2, 1)))

    assert client.is_closed


def test_cancellation_cleans_up(monkeypatch):
    async def scenario():
        active = 0
        workers_started = asyncio.Event()
        block = asyncio.Event()

        async def handler(request):
            nonlocal active
            active += 1
            if active == 2:
                workers_started.set()

            try:
                await block.wait()
                return httpx.Response(200)
            finally:
                active -= 1

        client = install_client(monkeypatch, handler)
        task = asyncio.create_task(http_runner.run_experiment(make_spec(10, 2)))

        await workers_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert active == 0
        assert client.is_closed

    asyncio.run(asyncio.wait_for(scenario(), timeout=3.0))
