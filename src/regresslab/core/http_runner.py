import asyncio
from time import perf_counter

import httpx

from regresslab.core.config import ExperimentSpec
from regresslab.core.models import Measurement, MeasurementStatus


async def measure_http_get(
    url: str,
    *,
    client: httpx.AsyncClient,
) -> Measurement:
    failure_detail: str | None = None
    started = perf_counter()

    try:
        response = await client.get(url, follow_redirects=False)
    except httpx.RequestError as exc:
        failure_detail = f"{type(exc).__name__}: {exc}"
    else:
        if not response.is_success:
            failure_detail = f"HTTP {response.status_code}"

    elapsed = perf_counter() - started
    succeeded = failure_detail is None

    return Measurement(
        latencies_ms=(elapsed * 1000,) if succeeded else (),
        request_count=1,
        successful_requests=1 if succeeded else 0,
        failed_requests=0 if succeeded else 1,
        elapsed_seconds=elapsed,
        status=(MeasurementStatus.SUCCESS if succeeded else MeasurementStatus.FAILED),
        failure_detail=failure_detail,
    )


async def run_experiment(spec: ExperimentSpec) -> Measurement:
    worker_count = min(spec.concurrency, spec.requests)
    request_numbers = iter(range(spec.requests))
    url = str(spec.url)

    latencies_ms: list[float] = []
    successful_requests = 0
    failed_requests = 0
    first_failure: str | None = None

    async with httpx.AsyncClient(
        timeout=5.0,
        limits=httpx.Limits(
            max_connections=worker_count,
            max_keepalive_connections=worker_count,
        ),
    ) as client:

        async def worker() -> None:
            nonlocal successful_requests, failed_requests, first_failure

            for _ in request_numbers:
                result = await measure_http_get(url, client=client)

                latencies_ms.extend(result.latencies_ms)
                successful_requests += result.successful_requests
                failed_requests += result.failed_requests

                if first_failure is None and result.failure_detail is not None:
                    first_failure = result.failure_detail

        started = perf_counter()

        async with asyncio.TaskGroup() as group:
            for _ in range(worker_count):
                group.create_task(worker())

        elapsed = perf_counter() - started

    return Measurement(
        latencies_ms=tuple(latencies_ms),
        request_count=successful_requests + failed_requests,
        successful_requests=successful_requests,
        failed_requests=failed_requests,
        elapsed_seconds=elapsed,
        status=MeasurementStatus.SUCCESS,
        failure_detail=(
            f"{failed_requests} requests failed; first error: {first_failure}"
            if failed_requests
            else None
        ),
    )
