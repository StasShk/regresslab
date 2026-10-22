# Compare saved benchmark series from the CLI

Use `regresslab compare-series` to compare **explicitly matched** baseline and
candidate results that were previously saved with `regresslab run --output`.
You do not need to write Python code.

## Create the manifest

Create `pairs.yaml` next to the saved results:

```yaml
pairs:
  - id: trial-1
    baseline: results/baseline-1.json
    candidate: results/candidate-1.json
  - id: trial-2
    baseline: results/baseline-2.json
    candidate: results/candidate-2.json
```

The input is a YAML mapping with a **nonempty** `pairs` list. Each pair must
have a unique, nonempty `id`, plus the paths `baseline` and `candidate`.
Relative paths are interpreted relative to the manifest file, not the current
working directory. Absolute paths also work. Input order is preserved.

The JSON files use the existing saved-result schema (`schema_version: 1`).
The `compare-series` command reads the saved metrics; it does not rerun HTTP
requests or recompute metrics from the raw latencies.

## Run the comparison

```sh
regresslab compare-series pairs.yaml --output comparison.json
```

Change the thresholds just as with the single-pair `compare` command:

```sh
regresslab compare-series pairs.yaml \
  --p95-latency-pct 10 \
  --throughput-pct 10 \
  --error-rate-points 1
```

The report displays each pair's signed changes and per-metric
`PASS`/`REGRESSION`/`INCONCLUSIVE` verdicts, followed by summary counts and
median/min/max of the **paired changes**. Positive throughput change denotes an
improvement; positive latency or error-rate change denotes worsening.
Changes for error rate are measured in **percentage points**; latency and
throughput changes are percentages.

Compatibility is checked both **within each pair** and **across all pairs**,
using the same workload checks as the existing `compare` command. Different
hosts and ports are permitted, while the experiment name, URL scheme/path/query,
request count, concurrency, and `timeout_seconds` must match. Older saved
results without `timeout_seconds` use the default of `5.0` seconds. You must
still ensure comparable runtime environments and meaningful baseline/candidate
pairing yourself.

## Exit codes and interpretation

`--output` is optional. It creates a UTF-8 JSON document with `schema_version: 1`
and a `series` object containing the thresholds, all pair IDs and comparison
results, and the per-metric summaries. Undefined changes remain JSON `null`;
individual reasons and verdicts are preserved. There is no series-wide status.
The JSON can be retained as a CI artifact or read by other programs.

The output path is relative to the current working directory. Its parent must
exist, and an existing file is never overwritten. This also prevents accidentally
replacing the manifest or an input result. Choose a new output filename for a
subsequent invocation. The terminal report is printed only after a requested
JSON export succeeds.

- `0`: the descriptive report was generated (even if individual pairs regressed).
- `2`: invalid manifest, missing/corrupt result file, incompatible experiments,
  or an input/output error (including an existing output file).

**There is deliberately no series-wide PASS/REGRESSION exit code.** The
summary does not perform a statistical test or infer an aggregate decision from
regression counts. A single pair can have a verdict, but it does not establish
that the entire series is statistically reliable. See
[comparison-series.md](comparison-series.md) for the Python API and measurement
caveats.

## Optional statistical gate

`compare-series` remains descriptive unless `--gate` is supplied. To turn the
same saved matched pairs into a CI gate:

```sh
regresslab compare-series results/pairs.yaml \
  --gate --min-pairs 8 --confidence 0.95 \
  --p95-latency-pct 10 --throughput-pct 10 --error-rate-points 1 \
  --output gate.json
```

The gate tests the **median paired worsening** (not the mean request latency,
not a pooled p95, and not the proportion of regressed individual runs).
Worsening is `100 * (candidate/baseline - 1)` for p95 latency,
`100 * (1 - candidate/baseline)` for successful throughput, and
`100 * (candidate_error_rate - baseline_error_rate)` in percentage points.
A positive value means worse performance for every metric.

Independent and comparably distributed *run pairs* are assumed. The gate uses
exact binomial order-statistic bounds for the population median. There are
three metrics and two one-sided bounds per metric; the Bonferroni allocation
is `(1 - confidence) / 6` per bound. This controls familywise false decisions
under those assumptions without bootstrap, random seeds, normality assumptions,
or an extra SciPy dependency. The reported interval is formed by its two
one-sided bounds. Its simultaneous coverage is at least the configured
confidence, and it may be conservative for small samples.

- `PASS` (exit `0`): all three upper bounds are strictly below their respective
  worsening thresholds.
- `REGRESSION` (exit `1`): any metric's lower bound is strictly above its
  threshold, even if another metric cannot be evaluated.
- `INCONCLUSIVE` (exit `3`): a boundary overlaps/touches its threshold, there
  are fewer than `--min-pairs` pairs, the confidence is too high for finite
  distribution-free bounds, or a necessary metric is missing or undefined.
- `ERROR` (exit `2`): invalid configuration, manifest/result, incompatible
  workloads, or file I/O failure.

For relative comparisons, *every* baseline must be positive: a baseline of
zero (including `0 -> 0` throughput) is undefined and cannot yield PASS.
Missing metrics are not silently removed; they make that metric INCONCLUSIVE.
`--min-pairs 8` is a collection floor, not a power guarantee; a high confidence
or high noise can require substantially more pairs. Exact threshold equality
remains INCONCLUSIVE. The gate does not compensate for serial correlation,
non-stationarity, shared infrastructure noise, or a mismatched workload.

In `--gate` mode, the JSON retains the unchanged `series` object and adds a
`gate` object with `status`, parameters, all three metric decisions, finite
one-sided bounds (or `null`), and reasons. Existing files are never overwritten.
Without `--gate`, the original JSON shape and exit codes are preserved. The
options `--min-pairs` and `--confidence` require `--gate`.
