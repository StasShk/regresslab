import pytest
from pydantic import ValidationError

from regresslab.core.compare import (
    ComparisonStatus,
    RegressionThresholds,
    compare_metrics,
)
from regresslab.core.metrics import BenchmarkMetrics, calculate_metrics
from regresslab.core.models import Measurement


def metrics(
    *,
    p95: float | None = 35.2,
    throughput: float | None = 100.0,
    error_rate: float | None = 0.02,
) -> BenchmarkMetrics:
    return BenchmarkMetrics(
        p50_ms=p95,
        p95_ms=p95,
        p99_ms=p95,
        successful_throughput_rps=throughput,
        error_rate=error_rate,
    )


@pytest.fixture
def thresholds() -> RegressionThresholds:
    return RegressionThresholds(p95_latency_pct=10, throughput_pct=10, error_rate_points=1)


def test_p95_regression_example(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(metrics(), metrics(p95=42.6), thresholds)

    assert result.p95_latency_ms.baseline == 35.2
    assert result.p95_latency_ms.candidate == 42.6
    assert result.p95_latency_ms.change_pct == pytest.approx(21.022727, rel=1e-5)
    assert result.p95_latency_ms.change_points is None
    assert result.p95_latency_ms.status is ComparisonStatus.REGRESSION
    assert result.status is ComparisonStatus.REGRESSION


def test_throughput_drop_is_a_regression(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(metrics(), metrics(throughput=85), thresholds)

    assert result.successful_throughput_rps.change_pct == -15.0
    assert result.successful_throughput_rps.status is ComparisonStatus.REGRESSION
    assert result.status is ComparisonStatus.REGRESSION


def test_error_rate_uses_percentage_points(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(metrics(error_rate=0.02), metrics(error_rate=0.035), thresholds)

    assert result.error_rate.change_points == pytest.approx(1.5)
    assert result.error_rate.change_pct is None
    assert result.error_rate.status is ComparisonStatus.REGRESSION
    assert result.status is ComparisonStatus.REGRESSION


def test_non_regressing_metrics_pass(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(
        metrics(p95=40, throughput=100, error_rate=0.04),
        metrics(p95=35, throughput=110, error_rate=0.01),
        thresholds,
    )

    assert result.status is ComparisonStatus.PASS
    assert all(
        metric.status is ComparisonStatus.PASS
        for metric in (
            result.p95_latency_ms,
            result.successful_throughput_rps,
            result.error_rate,
        )
    )


@pytest.mark.parametrize(
    ("before", "after", "field"),
    [
        (metrics(p95=100), metrics(p95=110), "p95_latency_ms"),
        (metrics(throughput=100), metrics(throughput=90), "successful_throughput_rps"),
        (metrics(error_rate=0.02), metrics(error_rate=0.03), "error_rate"),
    ],
)
def test_exact_threshold_is_not_a_regression(
    thresholds: RegressionThresholds,
    before: BenchmarkMetrics,
    after: BenchmarkMetrics,
    field: str,
) -> None:
    result = compare_metrics(before, after, thresholds)
    assert getattr(result, field).status is ComparisonStatus.PASS
    assert result.status is ComparisonStatus.PASS


@pytest.mark.parametrize(
    ("before", "after", "field"),
    [
        (metrics(p95=100), metrics(p95=110.1), "p95_latency_ms"),
        (metrics(throughput=100), metrics(throughput=89.9), "successful_throughput_rps"),
        (metrics(error_rate=0.02), metrics(error_rate=0.031), "error_rate"),
    ],
)
def test_just_over_threshold_is_a_regression(
    thresholds: RegressionThresholds,
    before: BenchmarkMetrics,
    after: BenchmarkMetrics,
    field: str,
) -> None:
    result = compare_metrics(before, after, thresholds)
    assert getattr(result, field).status is ComparisonStatus.REGRESSION
    assert result.status is ComparisonStatus.REGRESSION


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            metrics(p95=None),
            metrics(),
        ),
        (metrics(), metrics(p95=None)),
        (metrics(p95=None), metrics(p95=None)),
    ],
)
def test_missing_p95_is_inconclusive(
    thresholds: RegressionThresholds,
    before: BenchmarkMetrics,
    after: BenchmarkMetrics,
) -> None:
    result = compare_metrics(before, after, thresholds)

    assert result.p95_latency_ms.status is ComparisonStatus.INCONCLUSIVE
    assert result.p95_latency_ms.change_pct is None
    assert result.status is ComparisonStatus.INCONCLUSIVE


def test_missing_throughput_and_error_rates_are_inconclusive(
    thresholds: RegressionThresholds,
) -> None:
    result = compare_metrics(
        metrics(throughput=None, error_rate=None),
        metrics(),
        thresholds,
    )
    assert result.successful_throughput_rps.status is ComparisonStatus.INCONCLUSIVE
    assert result.error_rate.status is ComparisonStatus.INCONCLUSIVE
    assert result.status is ComparisonStatus.INCONCLUSIVE


def test_regression_takes_precedence_over_missing_metrics(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(metrics(p95=None), metrics(p95=70, throughput=80), thresholds)
    assert result.p95_latency_ms.status is ComparisonStatus.INCONCLUSIVE
    assert result.successful_throughput_rps.status is ComparisonStatus.REGRESSION
    assert result.status is ComparisonStatus.REGRESSION


def test_zero_baseline_throughput_is_handled(thresholds: RegressionThresholds) -> None:
    improving = compare_metrics(metrics(throughput=0), metrics(throughput=10), thresholds)
    both_zero = compare_metrics(metrics(throughput=0), metrics(throughput=0), thresholds)

    assert improving.successful_throughput_rps.status is ComparisonStatus.PASS
    assert improving.successful_throughput_rps.change_pct is None
    assert both_zero.successful_throughput_rps.status is ComparisonStatus.PASS
    assert both_zero.successful_throughput_rps.change_pct == 0


def test_zero_baseline_p95_cannot_be_compared_by_percentage(
    thresholds: RegressionThresholds,
) -> None:
    result = compare_metrics(metrics(p95=0), metrics(p95=10), thresholds)

    assert result.p95_latency_ms.status is ComparisonStatus.INCONCLUSIVE
    assert result.p95_latency_ms.change_pct is None
    assert result.status is ComparisonStatus.INCONCLUSIVE


def test_both_zero_p95_values_are_equal(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(metrics(p95=0), metrics(p95=0), thresholds)

    assert result.p95_latency_ms.status is ComparisonStatus.PASS
    assert result.p95_latency_ms.change_pct == 0


def test_all_missing_metrics_are_inconclusive(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(
        metrics(p95=None, throughput=None, error_rate=None),
        metrics(p95=None, throughput=None, error_rate=None),
        thresholds,
    )
    assert result.status is ComparisonStatus.INCONCLUSIVE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("p95_latency_pct", -1),
        ("throughput_pct", -1),
        ("error_rate_points", -1),
        ("error_rate_points", 101),
        ("p95_latency_pct", float("inf")),
        ("throughput_pct", float("nan")),
    ],
)
def test_rejects_invalid_thresholds(field: str, value: float) -> None:
    data = {"p95_latency_pct": 10, "throughput_pct": 10, "error_rate_points": 1}
    data[field] = value
    with pytest.raises(ValidationError):
        RegressionThresholds(**data)


def test_thresholds_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RegressionThresholds(
            p95_latency_pct=10, throughput_pct=10, error_rate_points=1, p99_latency_pct=10
        )


def test_comparison_models_are_frozen(thresholds: RegressionThresholds) -> None:
    result = compare_metrics(metrics(), metrics(), thresholds)

    with pytest.raises(ValidationError):
        result.status = ComparisonStatus.REGRESSION
    with pytest.raises(ValidationError):
        result.error_rate.status = ComparisonStatus.REGRESSION


def test_result_serializes_for_future_cli(thresholds: RegressionThresholds) -> None:
    data = compare_metrics(metrics(), metrics(p95=50), thresholds).model_dump(mode="json")

    assert data["status"] == "REGRESSION"
    assert data["p95_latency_ms"]["status"] == "REGRESSION"
    assert data["p95_latency_ms"]["change_pct"] == pytest.approx(42.0454545)


def test_comparison_accepts_calculated_metrics(thresholds: RegressionThresholds) -> None:
    baseline = Measurement(
        latencies_ms=(20.0, 30.0),
        request_count=2,
        successful_requests=2,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    candidate = Measurement(
        latencies_ms=(20.0, 40.0),
        request_count=2,
        successful_requests=2,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    result = compare_metrics(calculate_metrics(baseline), calculate_metrics(candidate), thresholds)

    assert result.p95_latency_ms.change_pct == pytest.approx(100 / 3)
    assert result.p95_latency_ms.status is ComparisonStatus.REGRESSION
    assert result.status is ComparisonStatus.REGRESSION
