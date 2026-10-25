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

The fingerprint hashes canonical, effective experiment settings, ignoring
baseline/candidate URL hosts, ports, and credentials. It includes the workload
URL scheme, path, query and execution limits. It is reproducible for the same
configuration across hosts and insensitive to YAML key order or overrides of
`environment` identifiers. It is not a cryptographic attestation: a digest
cannot prove that external load generators or infrastructure were identical.

```sh
regresslab compare baseline.json candidate.json
regresslab compare baseline.json candidate.json --strict-environment
regresslab compare-series pairs.yaml --gate --strict-environment
```

By default, mismatched OS, architecture, Python version, environment ID,
configuration fingerprint, or RegressLab version produce warnings on stderr,
not a changed benchmark verdict. Strict mode rejects any such mismatch, or
missing provenance from either input, with exit code 2. Series comparison also
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
