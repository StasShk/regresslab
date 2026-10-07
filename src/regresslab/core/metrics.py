from math import ceil, isfinite
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from regresslab.core.models import Measurement, NonNegativeFloat


class BenchmarkMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    p50_ms: NonNegativeFloat | None
    p95_ms: NonNegativeFloat | None
    p99_ms: NonNegativeFloat | None
    throughput_rps: NonNegativeFloat | None
    error_rate: Annotated[float, Field(ge=0, le=1)] | None


def calculate_metrics(measurement: Measurement) -> BenchmarkMetrics:
    """Calculate metrics using nearest-rank percentiles of successful requests."""
    if not isfinite(measurement.elapsed_seconds):
        raise ValueError("Elapsed time must be finite")

    if any(not isfinite(value) for value in measurement.latencies_ms):
        raise ValueError("Latencies must be finite")

    latencies = sorted(measurement.latencies_ms)

    def percentile(percent: int) -> float | None:
        if not latencies:
            return None
        index = ceil(len(latencies) * percent / 100) - 1
        return latencies[index]

    has_requests = measurement.request_count > 0

    return BenchmarkMetrics(
        p50_ms=percentile(50),
        p95_ms=percentile(95),
        p99_ms=percentile(99),
        throughput_rps=(
            measurement.successful_requests / measurement.elapsed_seconds if has_requests else None
        ),
        error_rate=(
            measurement.failed_requests / measurement.request_count if has_requests else None
        ),
    )
