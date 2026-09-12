import pytest
from pydantic import ValidationError

from regresslab.core.models import Measurement, MeasurementStatus


def test_successful_measurement_calculates_throughput() -> None:
    measurement = Measurement(
        latencies_ms=(10.0, 12.0, 11.0),
        request_count=100,
        successful_requests=98,
        failed_requests=2,
        elapsed_seconds=2.0,
        status=MeasurementStatus.SUCCESS,
    )

    assert measurement.throughput_rps == 49.0


def test_measurement_rejects_negative_latency() -> None:
    with pytest.raises(ValidationError):
        Measurement(
            latencies_ms=(10.0, -5.0, 11.0),
            request_count=3,
            successful_requests=3,
            failed_requests=0,
            elapsed_seconds=1.0,
            status=MeasurementStatus.SUCCESS,
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
        "status": MeasurementStatus.SUCCESS,
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
            status=MeasurementStatus.SUCCESS,
        )


def test_successful_measurement_requires_positive_elapsed_time() -> None:
    with pytest.raises(ValidationError):
        Measurement(
            latencies_ms=(),
            request_count=0,
            successful_requests=0,
            failed_requests=0,
            elapsed_seconds=0.0,
            status=MeasurementStatus.SUCCESS,
        )


def test_failed_measurement_can_have_zero_elapsed_time() -> None:
    measurement = Measurement(
        latencies_ms=(),
        request_count=0,
        successful_requests=0,
        failed_requests=0,
        elapsed_seconds=0.0,
        status=MeasurementStatus.FAILED,
        failure_detail="target crashed",
    )

    assert measurement.throughput_rps == 0.0


def test_failed_measurement_does_not_report_throughput() -> None:
    measurement = Measurement(
        latencies_ms=(10.0, 11.0),
        request_count=2,
        successful_requests=2,
        failed_requests=0,
        elapsed_seconds=1.0,
        status=MeasurementStatus.FAILED,
        failure_detail="benchmark process exited unexpectedly",
    )

    assert measurement.throughput_rps == 0.0


def test_measurement_is_immutable() -> None:
    measurement = Measurement(
        latencies_ms=(10.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
        status=MeasurementStatus.SUCCESS,
    )

    with pytest.raises(ValidationError):
        measurement.elapsed_seconds = 5.0


def test_measurement_status_serializes_as_string() -> None:
    measurement = Measurement(
        latencies_ms=(10.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
        status=MeasurementStatus.SUCCESS,
    )

    data = measurement.model_dump(mode="json")

    assert data["status"] == "success"
