"""Conservative, exact median gate for independently collected benchmark pairs.

No resampling assumptions, random seeds or pooled request-level observations.
The inferential unit is one matched baseline/candidate run pair.
"""

from collections.abc import Sequence
from math import isclose, isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict

from regresslab.core.compare import ComparisonStatus, RegressionThresholds
from regresslab.core.series import BenchmarkPair


class MetricGateResult(BaseModel):
    """Simultaneous one-sided confidence bounds for median paired worsening."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    unit: Literal["percent", "percentage_points"]
    threshold: float
    valid_pairs: int
    median_worsening: float | None
    lower_bound: float | None
    upper_bound: float | None
    status: ComparisonStatus
    reason: str | None = None


class StatisticalGateResult(BaseModel):
    """Series-level gate. ERROR is a CLI exit condition, not a statistical verdict."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    method: Literal["exact_median_sign"] = "exact_median_sign"
    confidence: float
    min_pairs: int
    pair_count: int
    alpha_per_bound: float
    p95_latency_ms: MetricGateResult
    successful_throughput_rps: MetricGateResult
    error_rate: MetricGateResult
    status: ComparisonStatus


def _order_statistic_rank(count: int, alpha: float) -> int:
    """Largest k with P[Binomial(n, 0.5) <= k-1] <= alpha.

    Integer arithmetic keeps the binomial tail exact even for large n; only
    the comparison against the requested floating-point alpha is inexact.
    A rank of zero means no finite distribution-free confidence bound exists.
    """
    numerator = 1  # C(n, 0)
    term = 1
    denominator = 1 << count
    rank = 0
    for k in range(1, count + 1):
        if numerator / denominator > alpha:
            break
        rank = k
        term = term * (count - k + 1) // k
        numerator += term
    return rank


def _worsening(pair: BenchmarkPair, metric: str) -> float | None:
    before: float | None
    after: float | None
    if metric == "error_rate":
        before, after = pair.baseline.error_rate, pair.candidate.error_rate
    elif metric == "p95_ms":
        before, after = pair.baseline.p95_ms, pair.candidate.p95_ms
    elif metric == "successful_throughput_rps":
        before = pair.baseline.successful_throughput_rps
        after = pair.candidate.successful_throughput_rps
    else:
        raise ValueError(f"Unknown gate metric: {metric}")
    if before is None or after is None:
        return None
    if metric == "error_rate":
        change = (after - before) * 100.0
    else:
        # A zero baseline has no relative scale. In particular, 0 -> 0
        # throughput cannot provide evidence for a relative-throughput gate.
        if before == 0:
            return None
        ratio = after / before
        change = (ratio - 1.0) * 100.0 if metric == "p95_ms" else (1.0 - ratio) * 100.0
    return change if isfinite(change) else None


def _gate_metric(
    pairs: Sequence[BenchmarkPair],
    metric: str,
    threshold: float,
    unit: Literal["percent", "percentage_points"],
    *,
    min_pairs: int,
    alpha: float,
) -> MetricGateResult:
    values = [_worsening(pair, metric) for pair in pairs]
    ordered = sorted(value for value in values if value is not None)
    count = len(ordered)
    middle = count // 2
    median = None
    if count:
        median = ordered[middle] if count % 2 else ordered[middle - 1] / 2.0 + ordered[middle] / 2.0
    reason = None
    lower = upper = None
    status = ComparisonStatus.INCONCLUSIVE
    if len(pairs) < min_pairs:
        reason = f"Need at least {min_pairs} pairs, got {len(pairs)}"
    elif count != len(pairs):
        reason = f"Missing or undefined relative metric in {len(pairs) - count} pair(s)"
    else:
        rank = _order_statistic_rank(count, alpha)
        if rank == 0:
            reason = "Too few pairs for finite bounds at the requested confidence"
        else:
            lower, upper = ordered[rank - 1], ordered[count - rank]
            if lower > threshold and not isclose(lower, threshold, rel_tol=1e-12):
                status = ComparisonStatus.REGRESSION
            elif upper < threshold and not isclose(upper, threshold, rel_tol=1e-12):
                status = ComparisonStatus.PASS
            else:
                reason = "Confidence bounds overlap or touch the regression threshold"
    return MetricGateResult(
        unit=unit,
        threshold=threshold,
        valid_pairs=count,
        median_worsening=median,
        lower_bound=lower,
        upper_bound=upper,
        status=status,
        reason=reason,
    )


def gate_series(
    pairs: Sequence[BenchmarkPair],
    thresholds: RegressionThresholds,
    *,
    min_pairs: int = 8,
    confidence: float = 0.95,
) -> StatisticalGateResult:
    """Classify the *median* paired worsening with conservative simultaneous bounds.

    The two one-sided median bounds for each of three metrics each spend
    (1-confidence)/6 familywise alpha (Bonferroni). Order statistics use an
    exact binomial sign-test inversion: no bootstrap calibration is required.
    Independent, comparably distributed *pairs* are still an assumption.
    """
    if type(min_pairs) is not int or min_pairs < 1:
        raise ValueError("min_pairs must be a positive integer")
    if isinstance(confidence, bool) or not isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("confidence must be finite and strictly between 0 and 1")
    if not pairs:
        raise ValueError("A series requires at least one pair")
    if len({pair.pair_id for pair in pairs}) != len(pairs):
        raise ValueError("Pair IDs must be unique within a series")

    alpha = (1.0 - confidence) / 6.0
    latency = _gate_metric(
        pairs, "p95_ms", thresholds.p95_latency_pct, "percent", min_pairs=min_pairs, alpha=alpha
    )
    throughput = _gate_metric(
        pairs,
        "successful_throughput_rps",
        thresholds.throughput_pct,
        "percent",
        min_pairs=min_pairs,
        alpha=alpha,
    )
    errors = _gate_metric(
        pairs,
        "error_rate",
        thresholds.error_rate_points,
        "percentage_points",
        min_pairs=min_pairs,
        alpha=alpha,
    )
    statuses = (latency.status, throughput.status, errors.status)
    if ComparisonStatus.REGRESSION in statuses:
        status = ComparisonStatus.REGRESSION
    elif ComparisonStatus.INCONCLUSIVE in statuses:
        status = ComparisonStatus.INCONCLUSIVE
    else:
        status = ComparisonStatus.PASS
    return StatisticalGateResult(
        confidence=confidence,
        min_pairs=min_pairs,
        pair_count=len(pairs),
        alpha_per_bound=alpha,
        p95_latency_ms=latency,
        successful_throughput_rps=throughput,
        error_rate=errors,
        status=status,
    )
