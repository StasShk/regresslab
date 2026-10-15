"""Descriptive summaries of explicit, matched benchmark pairs.

The caller establishes comparable workloads/environments and supplies pairing.
No samples are discarded, no requests are pooled across runs, and no statistical
confidence or series-wide PASS/REGRESSION decision is inferred from these counts.
"""

from collections.abc import Sequence
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from regresslab.core.compare import (
    ComparisonResult,
    ComparisonStatus,
    MetricComparison,
    RegressionThresholds,
    compare_metrics,
)
from regresslab.core.metrics import BenchmarkMetrics


class BenchmarkPair(BaseModel):
    """A matched baseline/candidate pair, identified by a caller-provided ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_id: str = Field(min_length=1)
    baseline: BenchmarkMetrics
    candidate: BenchmarkMetrics


class PairComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    pair_id: str
    comparison: ComparisonResult


class MetricSeriesSummary(BaseModel):
    """Signed changes: positive means an increase, including for throughput.

    Counts describe comparisons, not requests. ``defined_change_count`` can be
    smaller than pass_count + regression_count (e.g. throughput improves from zero).
    The median is a median of paired changes, never a pooled latency percentile.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    unit: Literal["percent", "percentage_points"]
    pass_count: int
    regression_count: int
    inconclusive_count: int
    defined_change_count: int
    median_change: float | None
    min_change: float | None
    max_change: float | None


class SeriesComparison(BaseModel):
    """All pair decisions and descriptive summaries under one set of thresholds."""

    model_config = ConfigDict(frozen=True)

    thresholds: RegressionThresholds
    pairs: tuple[PairComparison, ...]
    p95_latency_ms: MetricSeriesSummary
    successful_throughput_rps: MetricSeriesSummary
    error_rate: MetricSeriesSummary

    @property
    def pair_count(self) -> int:
        return len(self.pairs)


def _summarize(
    comparisons: Sequence[MetricComparison],
    unit: Literal["percent", "percentage_points"],
) -> MetricSeriesSummary:
    changes = [
        change
        for item in comparisons
        if (change := item.change_pct if unit == "percent" else item.change_points) is not None
    ]
    # Halving before addition avoids overflow when two large finite values
    # occupy the middle positions in an even-sized series.
    ordered = sorted(changes)
    middle = len(ordered) // 2
    median_change = None
    if ordered:
        median_change = (
            median(ordered) if len(ordered) % 2 else ordered[middle - 1] / 2 + ordered[middle] / 2
        )
    return MetricSeriesSummary(
        unit=unit,
        pass_count=sum(item.status is ComparisonStatus.PASS for item in comparisons),
        regression_count=sum(item.status is ComparisonStatus.REGRESSION for item in comparisons),
        inconclusive_count=sum(
            item.status is ComparisonStatus.INCONCLUSIVE for item in comparisons
        ),
        defined_change_count=len(changes),
        median_change=median_change,
        min_change=min(changes) if changes else None,
        max_change=max(changes) if changes else None,
    )


def compare_series(
    pairs: Sequence[BenchmarkPair], thresholds: RegressionThresholds
) -> SeriesComparison:
    """Compare every pair using compare_metrics(), retaining input order and IDs.

    At least one pair and unique IDs are required. One pair is descriptive only;
    even many pairs do not establish independence or causality. Missing metrics
    retain their existing INCONCLUSIVE semantics and are counted explicitly.
    """
    if not pairs:
        raise ValueError("A series requires at least one pair")
    if len({pair.pair_id for pair in pairs}) != len(pairs):
        raise ValueError("Pair IDs must be unique within a series")
    results = tuple(
        PairComparison(
            pair_id=pair.pair_id,
            comparison=compare_metrics(pair.baseline, pair.candidate, thresholds),
        )
        for pair in pairs
    )
    return SeriesComparison(
        thresholds=thresholds,
        pairs=results,
        p95_latency_ms=_summarize([p.comparison.p95_latency_ms for p in results], "percent"),
        successful_throughput_rps=_summarize(
            [p.comparison.successful_throughput_rps for p in results], "percent"
        ),
        error_rate=_summarize([p.comparison.error_rate for p in results], "percentage_points"),
    )
