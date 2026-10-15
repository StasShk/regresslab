import pytest
from pydantic import ValidationError

from regresslab.core.compare import ComparisonStatus, RegressionThresholds
from regresslab.core.metrics import BenchmarkMetrics
from regresslab.core.series import BenchmarkPair, compare_series


def metrics(p95: float | None, rps: float | None = 100, error: float | None = 0):
    return BenchmarkMetrics(
        p50_ms=None,
        p95_ms=p95,
        p99_ms=None,
        successful_throughput_rps=rps,
        error_rate=error,
    )


THRESHOLDS = RegressionThresholds(
    p95_latency_pct=20,
    throughput_pct=20,
    error_rate_points=1,
)


def test_single_spike_is_preserved_alongside_the_series_summary():
    pairs = tuple(
        BenchmarkPair(pair_id=str(i), baseline=metrics(a), candidate=metrics(b))
        for i, (a, b) in enumerate([(31, 34), (34, 38), (36, 91), (36, 34)])
    )
    result = compare_series(pairs, THRESHOLDS)
    summary = result.p95_latency_ms
    assert result.pair_count == 4
    assert [p.pair_id for p in result.pairs] == ["0", "1", "2", "3"]
    assert result.pairs[2].comparison.p95_latency_ms.candidate == 91
    assert result.pairs[2].comparison.status is ComparisonStatus.REGRESSION
    assert (summary.pass_count, summary.regression_count, summary.inconclusive_count) == (3, 1, 0)
    assert summary.defined_change_count == 4
    assert summary.median_change == pytest.approx(10.72106262)
    assert summary.min_change == pytest.approx(-5.55555556)
    assert summary.max_change == pytest.approx(152.77777778)
    assert result.thresholds == THRESHOLDS
    assert "status" not in result.model_dump()


def test_explicit_pairing_is_not_replaced_with_ratio_of_medians():
    result = compare_series(
        [
            BenchmarkPair(pair_id="a", baseline=metrics(10), candidate=metrics(20)),
            BenchmarkPair(pair_id="b", baseline=metrics(100), candidate=metrics(100)),
        ],
        THRESHOLDS,
    )
    assert result.p95_latency_ms.median_change == 50


def test_missing_and_undefined_changes_are_not_replaced_with_zero():
    result = compare_series(
        [
            BenchmarkPair(pair_id="missing", baseline=metrics(None), candidate=metrics(10)),
            BenchmarkPair(pair_id="zero", baseline=metrics(0, 0), candidate=metrics(10, 20)),
        ],
        THRESHOLDS,
    )
    assert result.p95_latency_ms.inconclusive_count == 2
    assert result.p95_latency_ms.defined_change_count == 0
    assert result.p95_latency_ms.median_change is None
    assert result.p95_latency_ms.min_change is None
    assert result.p95_latency_ms.max_change is None
    assert result.successful_throughput_rps.pass_count == 2
    assert result.successful_throughput_rps.defined_change_count == 1


def test_directions_units_and_existing_strict_threshold_are_preserved():
    result = compare_series(
        [
            BenchmarkPair(pair_id="boundary", baseline=metrics(100), candidate=metrics(120)),
            BenchmarkPair(
                pair_id="drop", baseline=metrics(100, 100, 0.02), candidate=metrics(130, 70, 0.04)
            ),
        ],
        THRESHOLDS,
    )
    assert result.p95_latency_ms.regression_count == 1
    assert result.successful_throughput_rps.regression_count == 1
    assert result.successful_throughput_rps.min_change == -30
    assert result.successful_throughput_rps.unit == "percent"
    assert result.error_rate.unit == "percentage_points"
    assert result.error_rate.max_change == 2


def test_empty_and_duplicate_series_are_rejected():
    with pytest.raises(ValueError, match="at least one"):
        compare_series([], THRESHOLDS)
    pair = BenchmarkPair(pair_id="same", baseline=metrics(10), candidate=metrics(10))
    with pytest.raises(ValueError, match="unique"):
        compare_series([pair, pair], THRESHOLDS)


def test_series_and_nested_pairs_are_frozen_and_round_trip():
    pair = BenchmarkPair(pair_id="one", baseline=metrics(10), candidate=metrics(10))
    result = compare_series([pair], THRESHOLDS)
    with pytest.raises(ValidationError):
        result.pairs[0].pair_id = "changed"
    assert type(result).model_validate_json(result.model_dump_json()) == result


def test_nonfinite_metric_input_is_rejected():
    with pytest.raises(ValidationError):
        metrics(float("nan"))


def test_median_of_large_finite_changes_does_not_overflow():
    result = compare_series(
        [
            BenchmarkPair(pair_id=str(i), baseline=metrics(1), candidate=metrics(1e306))
            for i in range(2)
        ],
        THRESHOLDS,
    )
    assert result.p95_latency_ms.median_change == pytest.approx(1e308)
