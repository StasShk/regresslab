import pytest
from pydantic import ValidationError

from regresslab.core.models import Measurement, RequestResult


def test_measurement_calculates_throughput() -> None:
    measurement = Measurement(
        latencies_ms=(10.0, 12.0, 11.0),
        request_count=5,
        successful_requests=3,
        failed_requests=2,
        elapsed_seconds=2.0,
    )

    assert measurement.throughput_rps == 1.5


def test_measurement_rejects_negative_latency() -> None:
    with pytest.raises(ValidationError):
        Measurement(
            latencies_ms=(10.0, -5.0, 11.0),
            request_count=3,
            successful_requests=3,
            failed_requests=0,
            elapsed_seconds=1.0,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_count", -1),
        ("successful_requests", -1),
        ("failed_requests", -1),
    ],
)
def test_measurement_rejects_negative_counters(
    field: str,
    value: int,
) -> None:
    data = {
        "latencies_ms": (10.0, 12.0),
        "request_count": 2,
        "successful_requests": 2,
        "failed_requests": 0,
        "elapsed_seconds": 1.0,
    }

    data[field] = value

    with pytest.raises(ValidationError):
        Measurement(**data)


def test_measurement_rejects_inconsistent_request_counts() -> None:
    with pytest.raises(ValidationError):
        Measurement(
            latencies_ms=(10.0, 12.0),
            request_count=10,
            successful_requests=8,
            failed_requests=1,
            elapsed_seconds=1.0,
        )


def test_measurement_rejects_latency_count_mismatch() -> None:
    with pytest.raises(
        ValidationError,
        match="latencies_ms must contain one entry per successful request",
    ):
        Measurement(
            latencies_ms=(10.0, 12.0, 11.0),
            request_count=100,
            successful_requests=98,
            failed_requests=2,
            elapsed_seconds=1.0,
        )


def test_measurement_with_requests_requires_positive_elapsed_time() -> None:
    with pytest.raises(ValidationError):
        Measurement(
            latencies_ms=(10.0,),
            request_count=1,
            successful_requests=1,
            failed_requests=0,
            elapsed_seconds=0.0,
        )


def test_empty_measurement_can_have_zero_elapsed_time() -> None:
    measurement = Measurement(
        latencies_ms=(),
        request_count=0,
        successful_requests=0,
        failed_requests=0,
        elapsed_seconds=0.0,
    )

    assert measurement.throughput_rps == 0.0


def test_measurement_is_immutable() -> None:
    measurement = Measurement(
        latencies_ms=(10.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
    )

    with pytest.raises(ValidationError):
        measurement.elapsed_seconds = 5.0


def test_successful_request_result() -> None:
    result = RequestResult(
        latency_ms=10.0,
        succeeded=True,
    )

    assert result.latency_ms == 10.0
    assert result.succeeded is True
    assert result.failure_detail is None


def test_successful_request_result_requires_latency() -> None:
    with pytest.raises(ValidationError, match="successful request requires latency_ms"):
        RequestResult(
            latency_ms=None,
            succeeded=True,
        )


def test_failed_request_result_rejects_latency() -> None:
    with pytest.raises(ValidationError, match="failed request must not have latency_ms"):
        RequestResult(
            latency_ms=10.0,
            succeeded=False,
            failure_detail="HTTP 500",
        )


def test_successful_request_result_rejects_failure_detail() -> None:
    with pytest.raises(ValidationError, match="successful request must not have failure_detail"):
        RequestResult(
            latency_ms=10.0,
            succeeded=True,
            failure_detail="unexpected detail",
        )
