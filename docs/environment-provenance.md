# Environment provenance

Saved result files (`schema_version: 1`) now include three optional top-level
fields: `environment`, `config_fingerprint`, and `regresslab_version`.
Legacy result files without all three fields continue to work by default.

Add optional, non-sensitive identifiers to either baseline or candidate YAML:

```yaml
name: orders
url: http://127.0.0.1:8000/orders
requests: 500
concurrency: 8
environment:
  git_sha: a1b2c3d
  service_revision: orders:v18
  environment_id: perf-runner-01
```

RegressLab automatically fills `runner_os`, `runner_arch`, and `python_version`
without reading hostnames, HTTP headers, environment variables, or credentials.
You can explicitly supply those three fields as well when the testbed differs
from the local CLI process (for example, a remote runner). The example adds:

```json
{
  "environment": {
    "git_sha": "a1b2c3d",
    "service_revision": "orders:v18",
    "runner_os": "linux",
    "runner_arch": "x86_64",
    "python_version": "3.12",
    "environment_id": "perf-runner-01"
  },
  "config_fingerprint": "sha256:<64 hex characters>",
  "regresslab_version": "0.1.0"
}
```

The above version and runner fields are illustrative; actual values come from
where RegressLab runs and the installed distribution.

The fingerprint uses the current workload-only algorithm.
It excludes endpoint host, port, credentials, environment identifiers, and
administrative safety caps (`max_requests`, `max_concurrency`, and
`max_result_bytes`). It retains workload settings such as concurrency, timeout,
and duration budget. On loading a result containing provenance, RegressLab
recomputes the fingerprint from its saved experiment. A mismatch is rejected
even without `--strict-environment`. Earlier fingerprint algorithms are not
supported. This is a consistency check, not cryptographic authentication.

```sh
regresslab compare baseline.json candidate.json
regresslab compare baseline.json candidate.json --strict-environment
regresslab compare-series pairs.yaml --gate --strict-environment
```

By default, mismatched OS, architecture, Python version, environment ID,
or RegressLab version produce warnings on stderr, not a changed benchmark
verdict. Strict mode rejects such mismatches or missing provenance, with exit
code 2. Workload compatibility is checked field by field; fingerprints are
verified against their own saved experiments, not compared across files.
Series comparison also
checks consistency across matching baseline roles and candidate roles of
successive pairs. `git_sha` and `service_revision` can differ because the
comparison is intended to detect regressions between software revisions.
Legacy-versus-legacy comparisons are silent by default; mixed old/new results
warn, and strict mode requires both results to carry provenance.

## Privacy and security

`environment` is a fixed allowlist of six short, structured identifiers. Unknown
fields (including `hostname`, `headers`, `authorization`, and arbitrary data)
are rejected. `environment_id` accepts only an opaque slug, not an FQDN or an
IP address. `service_revision` requires a namespaced `service:revision` token.
Do **not** supply credentials, tokens, sensitive IDs or hostname-derived names
through any of these fields.

Newly saved result JSON redacts the original benchmark URL's hostname, port,
username, password and fragment, replacing the endpoint with `redacted.invalid`.
The original `name`, URL path and ordinary query parameters remain, because
they are part of the workload comparison contract. The `run` terminal report
shows only the URL scheme and `[redacted]`, never the configured target address.
Handled HTTPX request-exception messages are not printed or persisted: diagnostics
retain only an allowlisted error class (such as `ReadTimeout` or `ConnectError`)
or HTTP status (such as `HTTP 503`). Even when a caller supplies a custom `Measurement`,
unknown `failure_detail` text is replaced with `redacted` before it is printed
or saved. Previously saved JSON is not rewritten.

Obvious authentication query keys (such as `token`, `password`, `secret`,
`api_key`, and `authorization`) are rejected before sending benchmark traffic
when saving results. This filter
**cannot detect arbitrary secrets in innocuous query keys, paths or names**.
Therefore, do not put secrets in benchmark configuration or publish result files
without reviewing them first. Previously saved artifacts are unchanged and may
still contain raw hostnames, userinfo and sensitive URL parameters; treat them
as confidential until separately sanitized.
