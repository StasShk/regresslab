# Warmup and execution budgets

The optional settings below apply to both `regresslab run` and each individual
benchmark executed by `regresslab run-series`. Existing YAML configurations
continue to work unchanged. There are **no new implicit limits**.

```yaml
name: orders-api
url: http://localhost:8000/orders
requests: 2000
concurrency: 10
timeout_seconds: 2.5
warmup_requests: 200
max_duration_seconds: 120
max_requests: 2200
max_concurrency: 10
max_result_bytes: 1048576
```

| Setting | Default | Behavior |
| --- | --- | --- |
| `warmup_requests` | `0` | Nonnegative number of unmeasured HTTP GETs before the benchmark |
| `max_duration_seconds` | `null` | Positive finite wall-clock deadline for warmup, measurement, and client cleanup |
| `max_requests` | `null` | Positive cap for `requests + warmup_requests` per run |
| `max_concurrency` | `null` | Positive cap for the configured `concurrency` per run |
| `max_result_bytes` | `null` | Positive cap on the complete UTF-8 encoded saved benchmark JSON in bytes |

`requests` remains the number of **measured** HTTP requests, not the total sent.
Warmup uses the same URL, GET method, HTTPX timeout, concurrency bound, and
client/connection pool as measurement. Warmup finishes before any measured
requests begin; its responses (including failures), latency, and elapsed time
are omitted from the measurement and throughput denominator. Failures during
warmup do not abort a run unless the overall duration budget is exceeded.
A new client is created for each run, so warmup can establish connections
within that run but does not persist connections across series legs.

`timeout_seconds` still configures **individual HTTPX connect/read/write/pool
operation timeouts**. It does not replace `max_duration_seconds`. Unlike an
HTTPX operation timeout (recorded as a failed request), expiration of the
whole-run deadline **aborts** collection, closes the client, and yields no
partial valid measurement. `run` exits `1`; `run-series` exits `2` and leaves
previously completed leg files without publishing `pairs.yaml`.

Invalid limits or `requests + warmup_requests > max_requests` or
`concurrency > max_concurrency` are rejected at configuration validation time,
before sending requests. `max_result_bytes` is checked on the exact serialized
UTF-8 JSON just before writing it; an oversized result fails instead of being
truncated or published. For `run --output`, the existing atomic publication
prevents a partial final file. For `run-series`, a failing leg does not publish
its JSON file or the manifest. A size limit does **not** limit RAM consumed
while collecting latency samples; it only bounds the saved document.

These caps apply **per run**, not across all `--repeats` in a series. For
example, `run-series --repeats 4` makes eight separate benchmark runs, so
`warmup_requests: 200` adds 1,600 warmup HTTP requests in total. To control
aggregate series cost, choose the `--repeats` count accordingly.

Comparisons require matching `warmup_requests` and `max_duration_seconds` across
baseline, candidate, and all runs in a series. Old saved results without those
fields read as `0` and `null`, respectively. `max_requests`, `max_concurrency`,
and `max_result_bytes` are safety caps; they need not match for comparison,
because they do not change completed measurements when their limits are not
exceeded.
