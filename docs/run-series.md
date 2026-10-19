# Collect repeated benchmark pairs

Use `run-series` when two versions of a service are already running and expose
the same endpoint. RegressLab collects matched measurements and writes the
manifest consumed by `compare-series`.

Create `baseline.yaml`:

```yaml
name: orders
url: http://localhost:8000/orders
requests: 1000
concurrency: 4
```

Create `candidate.yaml` with the same workload and a different service address:

```yaml
name: orders
url: http://localhost:8001/orders
requests: 1000
concurrency: 4
```

Collect four pairs, then compare the saved measurements:

```sh
uv run regresslab run-series baseline.yaml candidate.yaml \
  --repeats 4 --output-dir results/orders-001
uv run regresslab compare-series results/orders-001/pairs.yaml \
  --output results/orders-001/comparison.json
```

Both `--repeats` and `--output-dir` are required. Repeats must be a positive
integer. Four pairs mean eight benchmark runs; each run uses the configured
request count and concurrency. The example sends 8,000 requests in total.

## Execution and saved files

Runs are sequential, including the two runs within each pair. Odd pairs run
baseline then candidate; even pairs run candidate then baseline. Role labels
remain baseline and candidate regardless of execution order.

Before sending requests or creating the output directory, RegressLab checks
that the configurations have matching names, URL schemes, paths, query strings,
request counts, and concurrency. Hosts and ports may differ. This checks the
declared workload, not equivalence of the services or their environments.

The output directory must not exist, even if empty. Missing parent directories
are created. Each completed measurement is saved immediately as
`pair-0001-baseline.json`, `pair-0001-candidate.json`, and so on, using the same
JSON format as `run --output`. Files contain the configuration, measurement,
and calculated metrics.

`pairs.yaml` is published only after every pair completes. Its paths are
relative, so the entire result directory can be moved and compared offline.
JSON and manifest files are written through temporary files in the same
directory, then renamed. An interrupted write may leave a `.tmp` file; it is
not a completed measurement. This does not guarantee durability across power
loss.

On interruption or a collection error, completed JSON files remain available.
An unfinished series has no final `pairs.yaml`; a completed leg from an
unfinished pair is retained too. Automatic resume is not supported. Use a new
output directory to start a new series. To inspect retained measurements, use
`compare` on a completed pair or explicitly create a manifest for the pairs
you want to analyze.

## Exit codes and interpretation

- `0`: every measurement was collected and the manifest was saved.
- `2`: invalid arguments/configuration, incompatible workloads, or a file error.
- `130`: interrupted by Ctrl+C or asynchronous cancellation.

HTTP errors and connection failures are recorded as failed requests, as in
`run`; they do not by themselves abort collection. Exit code `0` is not a
performance verdict. Inspect error rates and missing metrics in the subsequent
comparison report.

This command uses the existing fixed-request HTTP runner. It does not start
services, warm them up, reset their state, or introduce delays between runs.
Each run creates a fresh HTTP client. Alternating order can reduce order bias,
but does not eliminate environmental noise or establish statistical confidence.
Series comparison remains descriptive, with no aggregate pass/fail verdict.
