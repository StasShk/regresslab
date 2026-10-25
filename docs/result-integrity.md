# Saved result integrity and legacy compatibility

`regresslab compare` and `regresslab compare-series` use the same saved-result
loader. If a schema v1 JSON contains a `measurement`, the loader validates
its counters, nonnegative and finite latencies, elapsed time, and consistency
with `experiment.requests`. It recomputes nearest-rank p50, p95, p99,
successful throughput, and error rate using the same implementation as `run`.
Every saved metric, including whether it is `null`, must match the recomputed
value within a relative tolerance of `1e-9` and absolute tolerance of `1e-12`.
Disagreements or a malformed `measurement` are input errors (exit code `2`),
not PASS or INCONCLUSIVE. Non-finite values are always invalid.

Older `schema_version: 1` results with **no `measurement` key** remain usable
with `compare` and descriptive `compare-series`, but their metrics cannot be
independently verified. Their absence of raw data is not treated as corruption.
A `measurement: null` or invalid object does **not** count as legacy.

Statistical `compare-series --gate` requires a valid measurement in **every**
file in the manifest, even if the file has environment provenance. To opt into
trusting old metrics-only files explicitly:

```sh
regresslab compare-series pairs.yaml --gate --allow-legacy-metrics
```

`--allow-legacy-metrics` cannot be used without `--gate`. It only allows files
whose measurement key is absent; it never suppresses validation of measurements
that are present or relaxes workload, provenance, or independent-file checks.
This policy does **not** provide authenticity or signatures: a consistently
rewritten file can still be forged. The aim is to catch internally inconsistent
or accidentally damaged saved results before using them for CI decisions.
