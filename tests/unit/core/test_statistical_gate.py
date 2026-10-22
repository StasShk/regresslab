"""Exact median gate tests, including independent false-alarm calibration."""

import random

import pytest

from regresslab.core.compare import ComparisonStatus, RegressionThresholds
from regresslab.core.metrics import BenchmarkMetrics
from regresslab.core.series import BenchmarkPair
from regresslab.core.statistical_gate import _order_statistic_rank, gate_series

THRESHOLDS = RegressionThresholds(p95_latency_pct=10, throughput_pct=10, error_rate_points=1)


def _metrics(
    p95: float | None = 100,
    throughput: float | None = 100,
    error_rate: float | None = 0,
) -> BenchmarkMetrics:
    return BenchmarkMetrics(
        p50_ms=p95,
        p95_ms=p95,
        p99_ms=p95,
        successful_throughput_rps=throughput,
        error_rate=error_rate,
    )


def _pairs(
    count: int = 8,
    *,
    p95: float | None = 100,
    throughput: float | None = 100,
    error_rate: float | None = 0,
) -> list[BenchmarkPair]:
    return [
        BenchmarkPair(
            pair_id=f"pair-{i}",
            baseline=_metrics(),
            candidate=_metrics(p95, throughput, error_rate),
        )
        for i in range(count)
    ]


def test_exact_order_statistic_bounds_and_minimum_sample_size() -> None:
    assert _order_statistic_rank(8, 0.05 / 6) == 1
    assert _order_statistic_rank(7, 0.05 / 6) == 1
    assert _order_statistic_rank(8, 0.01 / 6) == 0
    assert _order_statistic_rank(40, 0.05 / 6) > 1


def test_stable_series_passes_with_three_simultaneous_bounds() -> None:
    result = gate_series(_pairs(), THRESHOLDS)
    assert result.status is ComparisonStatus.PASS
    assert result.p95_latency_ms.upper_bound == 0
    assert result.successful_throughput_rps.upper_bound == 0
    assert result.error_rate.upper_bound == 0
    assert result.alpha_per_bound == pytest.approx(0.05 / 6)


@pytest.mark.parametrize(
    ("changed", "metric"),
    [
        ({"p95": 125}, "p95_latency_ms"),
        ({"throughput": 70}, "successful_throughput_rps"),
        ({"error_rate": 0.04}, "error_rate"),
    ],
)
def test_controlled_regression_exceeds_lower_bound(changed: dict[str, float], metric: str) -> None:
    result = gate_series(_pairs(**changed), THRESHOLDS)
    assert result.status is ComparisonStatus.REGRESSION
    assert getattr(result, metric).status is ComparisonStatus.REGRESSION
    assert getattr(result, metric).lower_bound > getattr(result, metric).threshold


def test_mixed_noise_is_inconclusive_not_a_false_pass() -> None:
    pairs = _pairs()
    for i in range(4):
        pairs[i] = pairs[i].model_copy(update={"candidate": _metrics(p95=120)})
    result = gate_series(pairs, THRESHOLDS)
    assert result.status is ComparisonStatus.INCONCLUSIVE
    assert result.p95_latency_ms.lower_bound < 10 < result.p95_latency_ms.upper_bound


def test_threshold_equality_is_inconclusive() -> None:
    result = gate_series(_pairs(p95=110, throughput=90, error_rate=0.01), THRESHOLDS)
    assert result.status is ComparisonStatus.INCONCLUSIVE
    assert result.p95_latency_ms.status is ComparisonStatus.INCONCLUSIVE
    assert result.successful_throughput_rps.status is ComparisonStatus.INCONCLUSIVE
    assert result.error_rate.status is ComparisonStatus.INCONCLUSIVE


@pytest.mark.parametrize("n", [1, 5, 7])
def test_too_few_pairs_never_pass_even_if_identical(n: int) -> None:
    result = gate_series(_pairs(n), THRESHOLDS)
    assert result.status is ComparisonStatus.INCONCLUSIVE
    assert "at least 8" in (result.error_rate.reason or "")


def test_high_confidence_can_require_more_than_eight_pairs() -> None:
    result = gate_series(_pairs(), THRESHOLDS, confidence=0.99)
    assert result.status is ComparisonStatus.INCONCLUSIVE
    assert result.p95_latency_ms.lower_bound is None


@pytest.mark.parametrize("baseline,candidate", [(0.0, 0.0), (0.0, 90.0)])
def test_zero_baseline_throughput_is_undefined(baseline: float, candidate: float) -> None:
    pairs = _pairs()
    pairs = [
        pair.model_copy(
            update={
                "baseline": _metrics(throughput=baseline),
                "candidate": _metrics(throughput=candidate),
            }
        )
        for pair in pairs
    ]
    result = gate_series(pairs, THRESHOLDS)
    assert result.status is ComparisonStatus.INCONCLUSIVE
    assert result.successful_throughput_rps.valid_pairs == 0


def test_candidate_zero_throughput_is_detected_as_regression() -> None:
    result = gate_series(_pairs(throughput=0), THRESHOLDS)
    assert result.status is ComparisonStatus.REGRESSION
    assert result.successful_throughput_rps.median_worsening == 100


def test_missing_metric_is_not_silently_dropped() -> None:
    pairs = _pairs()
    pairs[0] = pairs[0].model_copy(update={"candidate": _metrics(p95=None)})
    result = gate_series(pairs, THRESHOLDS)
    assert result.status is ComparisonStatus.INCONCLUSIVE
    assert result.p95_latency_ms.valid_pairs == 7
    assert result.p95_latency_ms.lower_bound is None
    assert result.p95_latency_ms.reason is not None


def test_confirmed_regression_takes_precedence_over_missing_metric() -> None:
    result = gate_series(_pairs(p95=None, throughput=60), THRESHOLDS)
    assert result.status is ComparisonStatus.REGRESSION
    assert result.p95_latency_ms.status is ComparisonStatus.INCONCLUSIVE


@pytest.mark.parametrize("invalid", [0, -1, True, 3.5])
def test_invalid_min_pairs_rejected(invalid: float | bool) -> None:
    with pytest.raises(ValueError, match="min_pairs"):
        gate_series(_pairs(), THRESHOLDS, min_pairs=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [0, 1, -0.1, 1.01, float("nan"), float("inf")])
def test_invalid_confidence_rejected(invalid: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        gate_series(_pairs(), THRESHOLDS, confidence=invalid)


def _draw_noise(rng: random.Random, *, skewed: bool) -> float:
    amplitude = rng.lognormvariate(-1, 1) if skewed else abs(rng.gauss(0, 0.5))
    return amplitude if rng.random() < 0.5 else -amplitude


def test_randomized_familywise_false_regression_rate_at_threshold() -> None:
    """Independent experiments at the null boundary must rarely false-alarm.

    Effects are generated directly from independent matched-pair fluctuations.
    Two noise families include Gaussian and heavy-tailed lognormal signs;
    thresholds are the *true medians* in both. This probes familywise
    false regression alarms across all three metrics, not within one series.
    """
    trials = 1200
    for seed, skewed in [(77, False), (87, True)]:
        rng = random.Random(seed)
        false_regressions = 0
        for _ in range(trials):
            pairs = []
            for i in range(20):
                # Independent symmetric signs imply the median is at the threshold.
                lat_change = 10 + 10 * _draw_noise(rng, skewed=skewed)
                thr_change = 10 + 10 * _draw_noise(rng, skewed=skewed)
                error_change = 1 + _draw_noise(rng, skewed=skewed)
                pairs.append(
                    BenchmarkPair(
                        pair_id=f"p{i}",
                        baseline=_metrics(p95=100, throughput=100, error_rate=0.2),
                        candidate=_metrics(
                            p95=max(0, 100 * (1 + lat_change / 100)),
                            throughput=max(0, 100 * (1 - thr_change / 100)),
                            error_rate=max(0, min(1, 0.2 + error_change / 100)),
                        ),
                    )
                )
            if gate_series(pairs, THRESHOLDS).status is ComparisonStatus.REGRESSION:
                false_regressions += 1
        # Familywise target is at most 5%; margin avoids a flaky Monte Carlo test.
        assert false_regressions / trials < 0.07
