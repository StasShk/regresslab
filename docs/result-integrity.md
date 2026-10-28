# Saved result integrity

Both `compare` and `compare-series` require a saved benchmark JSON with
`experiment`, `measurement` and `metrics` (schema version 1). Missing or
invalid measurements are rejected (exit code `2`), including for descriptive
comparisons. The statistical gate does not accept metrics-only inputs.

The loader validates measurement counters, nonnegative finite latencies,
positive elapsed time, and consistency with `experiment.requests`.
It recalculates p50, p95 and p99 (nearest-rank), successful throughput and
error rate. Saved metric values must match to relative tolerance `1e-9`
and absolute tolerance `1e-12`. Invalid data cannot produce PASS.

This detects accidental corruption, not maliciously forged consistent files.
