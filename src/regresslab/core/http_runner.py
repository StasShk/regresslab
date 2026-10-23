import asyncio
from time import perf_counter

import httpx

from regresslab.core.config import ExperimentSpec
from regresslab.core.models import Measurement, RequestResult


async def measure_http_get(
    url: str,
    *,
    client: httpx.AsyncClient,
) -> RequestResult:
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

    return RequestResult(
        latency_ms=elapsed * 1000 if succeeded else None,
        succeeded=succeeded,
        failure_detail=failure_detail,
    )


class BenchmarkDeadlineExceeded(ValueError):
    """The whole benchmark exceeded its duration budget; no partial metrics are valid."""


async def run_experiment(spec: ExperimentSpec) -> Measurement:
    """Warm up with the same HTTP client, then measure exactly ``requests`` calls.

    A configured duration budget covers warmup, measurement, and client cleanup.
    Exceeding it aborts the entire run instead of returning biased partial data.
    """
    url = str(spec.url)
    latencies_ms: list[float] = []
    successful_requests = 0
    failed_requests = 0
    first_failure: str | None = None

    async def execute() -> Measurement:
        nonlocal successful_requests, failed_requests, first_failure
        worker_count = min(spec.concurrency, max(spec.requests, spec.warmup_requests))
        async with httpx.AsyncClient(
            timeout=spec.timeout_seconds,
            limits=httpx.Limits(
                max_connections=worker_count,
                max_keepalive_connections=worker_count,
            ),
        ) as client:

            async def phase(count: int, *, measured: bool) -> None:
                request_numbers = iter(range(count))

                async def worker() -> None:
                    nonlocal successful_requests, failed_requests, first_failure
                    for _ in request_numbers:
                        result = await measure_http_get(url, client=client)
                        if not measured:
                            continue  # Warmup responses and failures do not affect measurements.
                        if result.succeeded:
                            if result.latency_ms is None:
                                raise RuntimeError("Successful request result is missing latency")
                            latencies_ms.append(result.latency_ms)
                            successful_requests += 1
                        else:
                            failed_requests += 1
                            if first_failure is None and result.failure_detail is not None:
                                first_failure = result.failure_detail

                async with asyncio.TaskGroup() as group:
                    pending = {
                        group.create_task(worker()) for _ in range(min(spec.concurrency, count))
                    }
                    while pending:
                        done, pending = await asyncio.wait(
                            pending, return_when=asyncio.FIRST_COMPLETED
                        )
                        # TaskGroup does not propagate individual worker cancellation.
                        if any(task.cancelled() for task in done):
                            raise asyncio.CancelledError("Experiment worker was cancelled")

            if spec.warmup_requests:
                await phase(spec.warmup_requests, measured=False)

            # Only the measured phase contributes to elapsed_seconds/throughput.
            started = perf_counter()
            await phase(spec.requests, measured=True)
            elapsed = perf_counter() - started

        return Measurement(
            latencies_ms=tuple(latencies_ms),
            request_count=spec.requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            elapsed_seconds=elapsed,
            failure_detail=(
                f"{failed_requests} requests failed; first error: {first_failure}"
                if failed_requests
                else None
            ),
        )

    if spec.max_duration_seconds is None:
        return await execute()
    try:
        async with asyncio.timeout(spec.max_duration_seconds):
            return await execute()
    except TimeoutError as exc:
        raise BenchmarkDeadlineExceeded(
            f"Benchmark exceeded max_duration_seconds={spec.max_duration_seconds:g} "
            "(including warmup)"
        ) from exc
