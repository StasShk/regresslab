# Comparing repeated benchmark pairs

`compare_series()` extends the existing single-pair comparison with descriptive
summaries. It does not introduce a statistical test or a series-wide CI verdict.

```python
from regresslab.core.compare import RegressionThresholds
from regresslab.core.metrics import BenchmarkMetrics
from regresslab.core.series import BenchmarkPair, compare_series


def metrics(p95: float) -> BenchmarkMetrics:
    return BenchmarkMetrics(
        p50_ms=None,
        p95_ms=p95,
        p99_ms=None,
        successful_throughput_rps=None,
        error_rate=None,
    )


result = compare_series(
    [
        BenchmarkPair(pair_id="repeat-1", baseline=metrics(36), candidate=metrics(34)),
        BenchmarkPair(pair_id="repeat-2", baseline=metrics(36), candidate=metrics(91)),
    ],
    RegressionThresholds(p95_latency_pct=20, throughput_pct=20, error_rate_points=1),
)

assert result.p95_latency_ms.regression_count == 1
assert result.p95_latency_ms.pass_count == 1
assert result.successful_throughput_rps.inconclusive_count == 2
print(result.model_dump_json(indent=2))
```

Pair IDs must be unique and at least one pair is required. Pair order is retained.
Every pair is evaluated with the same thresholds through `compare_metrics()`.
Threshold equality remains PASS, including the existing floating-point tolerance.
No outliers are removed. The caller must verify workload and environment
compatibility before grouping runs; metrics alone cannot establish compatibility.
Do not combine different endpoints, configurations or generators in one series.

Each metric summary exposes PASS/REGRESSION/INCONCLUSIVE counts, the count of
defined changes, and the median/min/max of **paired changes**. Undefined changes
are not filled with zero. They remain visible in the individual results. A PASS
can have an undefined percentage (throughput improving from zero), so the number
of defined changes is not necessarily the number of conclusive comparisons.
Throughput changes retain their sign: a negative value is a decrease. Error-rate
changes use percentage points, while latency and throughput changes use percent.

The median is not a pooled p95, a confidence interval, a new threshold decision,
or evidence that a code change caused a slowdown. Repeats are not assumed
independent. Request counts are never used to weight pairs.
