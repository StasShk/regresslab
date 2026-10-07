import pytest
from pydantic import ValidationError

from regresslab.core.metrics import calculate_metrics
from regresslab.core.models import Measurement


def make_measurement(
    latencies=(),
    failed=0,
    elapsed=2.0,
):
    return Measurement(
        latencies_ms=latencies,
        request_count=len(latencies) + failed,
        successful_requests=len(latencies),
        failed_requests=failed,
        elapsed_seconds=elapsed,
    )


def test_calculates_known_metrics():
    # Twenty successful requests: 200, 190, ..., 10 ms.
    latencies = tuple(float(value) for value in range(200, 0, -10))
    measurement = make_measurement(latencies, failed=5, elapsed=2.0)

    metrics = calculate_metrics(measurement)

    assert metrics.p50_ms == 100.0
    assert metrics.p95_ms == 190.0
    assert metrics.p99_ms == 200.0
    assert metrics.throughput_rps == 10.0
    assert metrics.error_rate == pytest.approx(0.2)
    assert measurement.latencies_ms == latencies


@pytest.mark.parametrize(
    ("latencies", "expected"),
    [
        ((12.5,), (12.5, 12.5, 12.5)),
        ((10.0, 20.0), (10.0, 20.0, 20.0)),
    ],
)
def test_small_samples(latencies, expected):
    metrics = calculate_metrics(make_measurement(latencies))

    assert (metrics.p50_ms, metrics.p95_ms, metrics.p99_ms) == expected


def test_all_requests_failed():
    # The run completed, but every request failed.
    metrics = calculate_metrics(make_measurement(failed=3))

    assert metrics.p50_ms is None
    assert metrics.p95_ms is None
    assert metrics.p99_ms is None
    assert metrics.throughput_rps == 0.0
    assert metrics.error_rate == 1.0


def test_no_requests():
    metrics = calculate_metrics(make_measurement())

    assert metrics.p50_ms is None
    assert metrics.p95_ms is None
    assert metrics.p99_ms is None
    assert metrics.throughput_rps is None
    assert metrics.error_rate is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "latencies_ms",
            (float("inf"),),
            "Latencies must be finite",
        ),
        (
            "elapsed_seconds",
            float("inf"),
            "Elapsed time must be finite",
        ),
    ],
)
def test_rejects_unusable_measurement(field, value, message):
    data = make_measurement((10.0,)).model_dump()
    data[field] = value
    measurement = Measurement.model_validate(data)

    with pytest.raises(ValueError, match=message):
        calculate_metrics(measurement)


def test_metrics_are_frozen():
    metrics = calculate_metrics(make_measurement((10.0,)))

    with pytest.raises(ValidationError) as exc:
        metrics.p95_ms = 999.0

    assert exc.value.errors()[0]["type"] == "frozen_instance"
