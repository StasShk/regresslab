# RegressLab

RegressLab is a tool for detecting performance regressions in software systems.

The project is currently under development.

## Collect repeated benchmark pairs

With baseline and candidate services already running, collect matched pairs:

```sh
regresslab run-series baseline.yaml candidate.yaml --repeats 4 --output-dir results/orders-001
regresslab compare-series results/orders-001/pairs.yaml --output results/orders-001/comparison.json
```

Runs alternate baseline/candidate execution order. Each measurement is saved;
the final manifest is published only when the entire series completes.
See [the collection guide](docs/run-series.md) for configuration examples,
interruption behavior, and limitations.

## Configure HTTP timeouts

Set `timeout_seconds` in the experiment YAML used by `run` or `run-series`:

```yaml
name: orders
url: http://localhost:8000/orders
requests: 1000
concurrency: 4
timeout_seconds: 2.5
```

The value must be a finite number greater than zero (integers are accepted;
strings, booleans, and `null` are rejected). Omitting it keeps the default of
`5.0` seconds. The value applies to HTTPX's connect, read, write, and connection
pool timeouts. Read/write timeouts limit waiting for each chunk of data; this
is not a deadline for the entire request or benchmark.

A timed-out request is counted as failed and excluded from latency percentiles;
collection continues and the error rate includes that failure. A completed
collection can still return exit code `0` when requests time out.

Saved results include `timeout_seconds`. Comparisons require matching timeouts,
including across all pairs in a series, because the setting affects measured
errors and throughput. Older results without the field are interpreted as
`5.0` seconds and remain comparable to new results using that default.

## Compare saved benchmark results

Save benchmark runs with `regresslab run experiment.yaml --output run.json`,
then compare a pair with `regresslab compare baseline.json candidate.json`.
For `run --output`, choose a new filename in an existing directory. Existing
files (including symbolic links) are rejected before sending requests. Results
are written to temporary storage and published only after the complete JSON is
saved; a file created by another process during the run is never overwritten.
The output filesystem must support hard links, which is checked before the run.
Failures return exit code `1`; temporary storage is cleaned up on ordinary
errors and cancellation. Forced termination can leave a temporary directory,
and this mechanism does not guarantee durability across power loss.

To summarize several matched pairs from a YAML manifest **without writing Python**:

```sh
regresslab compare-series pairs.yaml --output comparison.json
```

See [the series CLI guide](docs/series-cli.md) for the manifest format, examples,
compatibility rules, and exit-code semantics.

For a CI decision over repeated matched pairs, add `--gate` (requires at least
8 complete pairs by default). The statistical gate returns `0=PASS`,
`1=REGRESSION`, `2=ERROR`, or `3=INCONCLUSIVE` and adds a `gate` object to the
JSON output. The ordinary descriptive `compare-series` stays unchanged.
See [statistical gate details](docs/series-cli.md#optional-statistical-gate).
