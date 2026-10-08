"""Compare benchmark metrics using explicit regression thresholds.

Percentage thresholds are relative to the baseline. Error-rate thresholds are
absolute percentage points (e.g. 1 means an increase from 2% to over 3%).
"""

from enum import StrEnum
from math import isclose, isfinite
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from regresslab.core.metrics import BenchmarkMetrics
from regresslab.core.models import NonNegativeFloat


class ComparisonStatus(StrEnum):
    PASS = "PASS"
    REGRESSION = "REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"


class RegressionThresholds(BaseModel):
    """Maximum tolerated worsening: percentages for latency/throughput, points for errors."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    p95_latency_pct: NonNegativeFloat
    throughput_pct: NonNegativeFloat
    error_rate_points: Annotated[float, Field(ge=0, le=100)]


class MetricComparison(BaseModel):
    """One metric's original values, change, and verdict.

    ``change_pct`` is used for latency/throughput; ``change_points`` for
    error rate. A missing change means a percentage comparison was undefined.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    baseline: NonNegativeFloat | None
    candidate: NonNegativeFloat | None
    change_pct: float | None = None
    change_points: float | None = None
    status: ComparisonStatus
    reason: str | None = None


class ComparisonResult(BaseModel):
    """Per-metric verdicts and the overall regression decision."""

    model_config = ConfigDict(frozen=True)

    p95_latency_ms: MetricComparison
    successful_throughput_rps: MetricComparison
    error_rate: MetricComparison
    status: ComparisonStatus


def _exceeds_threshold(worsening: float, threshold: float) -> bool:
    """Ignore floating-point rounding at a threshold, not real worsening.

    No absolute tolerance: a zero threshold still detects tiny regressions.
    """
    return worsening > threshold and not isclose(worsening, threshold, rel_tol=1e-12, abs_tol=0.0)


def _percentage_comparison(
    baseline: float | None,
    candidate: float | None,
    threshold_pct: float,
    *,
    higher_is_worse: bool,
) -> MetricComparison:
    if baseline is None or candidate is None:
        return MetricComparison(
            baseline=baseline,
            candidate=candidate,
            status=ComparisonStatus.INCONCLUSIVE,
            reason="Baseline or candidate metric is missing",
        )

    if baseline == 0:
        if candidate == 0:
            return MetricComparison(
                baseline=baseline,
                candidate=candidate,
                change_pct=0.0,
                status=ComparisonStatus.PASS,
            )
        if not higher_is_worse:
            # Moving from zero to positive successful throughput cannot be a regression.
            return MetricComparison(
                baseline=baseline,
                candidate=candidate,
                status=ComparisonStatus.PASS,
                reason="Throughput improved from zero; percentage change is undefined",
            )
        return MetricComparison(
            baseline=baseline,
            candidate=candidate,
            status=ComparisonStatus.INCONCLUSIVE,
            reason="Cannot calculate relative latency increase from a zero baseline",
        )

    change_pct = (candidate - baseline) / baseline * 100
    if not isfinite(change_pct):
        return MetricComparison(
            baseline=baseline,
            candidate=candidate,
            status=ComparisonStatus.INCONCLUSIVE,
            reason="Percentage change is not finite",
        )

    worsening_pct = change_pct if higher_is_worse else -change_pct
    is_regression = _exceeds_threshold(worsening_pct, threshold_pct)
    return MetricComparison(
        baseline=baseline,
        candidate=candidate,
        change_pct=change_pct,
        status=ComparisonStatus.REGRESSION if is_regression else ComparisonStatus.PASS,
    )


def _error_rate_comparison(
    baseline: float | None,
    candidate: float | None,
    threshold_points: float,
) -> MetricComparison:
    if baseline is None or candidate is None:
        return MetricComparison(
            baseline=baseline,
            candidate=candidate,
            status=ComparisonStatus.INCONCLUSIVE,
            reason="Baseline or candidate error rate is missing",
        )

    change_points = (candidate - baseline) * 100
    return MetricComparison(
        baseline=baseline,
        candidate=candidate,
        change_points=change_points,
        status=(
            ComparisonStatus.REGRESSION
            if _exceeds_threshold(change_points, threshold_points)
            else ComparisonStatus.PASS
        ),
    )


def compare_metrics(
    baseline: BenchmarkMetrics,
    candidate: BenchmarkMetrics,
    thresholds: RegressionThresholds,
) -> ComparisonResult:
    """Flag regressions only when worsening strictly exceeds the threshold.

    A clear regression wins over missing metrics; otherwise any unavailable
    comparison makes the overall result inconclusive.
    """
    p95 = _percentage_comparison(
        baseline.p95_ms,
        candidate.p95_ms,
        thresholds.p95_latency_pct,
        higher_is_worse=True,
    )
    throughput = _percentage_comparison(
        baseline.successful_throughput_rps,
        candidate.successful_throughput_rps,
        thresholds.throughput_pct,
        higher_is_worse=False,
    )
    error_rate = _error_rate_comparison(
        baseline.error_rate,
        candidate.error_rate,
        thresholds.error_rate_points,
    )

    statuses = (p95.status, throughput.status, error_rate.status)
    if ComparisonStatus.REGRESSION in statuses:
        status = ComparisonStatus.REGRESSION
    elif ComparisonStatus.INCONCLUSIVE in statuses:
        status = ComparisonStatus.INCONCLUSIVE
    else:
        status = ComparisonStatus.PASS

    return ComparisonResult(
        p95_latency_ms=p95,
        successful_throughput_rps=throughput,
        error_rate=error_rate,
        status=status,
    )
