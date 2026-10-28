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

## Benchmark HTTP methods and JSON requests

`method` defaults to `GET`. Supported methods are `GET`, `POST`, `PUT`,
`PATCH`, and `DELETE`. Use `json_body` to send an optional JSON object:

```yaml
name: create-order
url: http://localhost:8000/orders
method: POST
json_body:
  product_id: 123
  quantity: 2
requests: 1000
concurrency: 4
```

The same method and JSON body must be used for baseline and candidate;
both are part of workload identity and the configuration fingerprint.
The body is also sent during warmup. JSON bodies are saved in benchmark
results, so **do not put secrets or personal data in `json_body`**.
State-changing requests can affect later measurements; use isolated test
data and reset the target service when necessary.

## Benchmark protected APIs

Set `bearer_token_env: REGRESSLAB_API_TOKEN` in the experiment YAML and supply
that environment variable through your shell or CI secret store. The runner
uses the token for warmup and measured requests; saved results contain only
the variable name. See [Bearer authentication](docs/authentication.md) for
validation, comparison rules, and failure behavior.

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

## Warmup and execution budgets

`warmup_requests` runs unmeasured HTTP requests before each benchmark; an optional
`max_duration_seconds` bounds the **entire run**, including warmup. You can also
configure `max_requests`, `max_concurrency`, and `max_result_bytes` as safety
caps. Defaults preserve earlier behavior. See the
[execution budgets guide](docs/execution-budgets.md).

## Environment provenance

Optional `environment` identifiers in the experiment YAML are attached to saved
results along with automatically detected OS, architecture, Python version,
RegressLab version, and a deterministic SHA-256 configuration fingerprint.
The comparison commands warn about testbed differences; add
`--strict-environment` to reject missing or mismatched testbed metadata. Code
and service revisions are intentionally allowed to differ between baseline
and candidate. See [environment provenance](docs/environment-provenance.md) for
examples, privacy limits, and compatibility behavior.

## Compare saved benchmark results

Saved results must include measurements. The loader checks counters and
recomputes metrics before comparisons. See
[the result integrity guide](docs/result-integrity.md) for details.

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
