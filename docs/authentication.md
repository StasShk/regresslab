# Benchmark a protected API

Set the name of an environment variable in each experiment YAML:

```yaml
name: orders
url: https://staging.example.com/orders
requests: 1000
concurrency: 4
warmup_requests: 100
bearer_token_env: REGRESSLAB_API_TOKEN
```

Supply `REGRESSLAB_API_TOKEN` through your shell or CI secret store before
running `regresslab run` or `regresslab run-series`. Store only the variable
name in YAML, never the token itself. Use HTTPS for remote protected APIs;
HTTP remains available for local test servers.

The runner sends `Authorization: Bearer <token>` on every warmup and measured
request. Redirects are not followed. URL credentials cannot be combined with
Bearer authentication. The token must be nonempty and match RFC 6750 Bearer
token syntax; whitespace, control characters, and non-ASCII characters are
rejected without printing the value. No token acquisition or refresh is
performed. Use a credential valid for the whole benchmark.

An unset or invalid credential stops the run before HTTP requests. Series
collection checks both credential sources before creating its output directory
or starting the baseline. Credentials are resolved again before each run.
Exit codes are `1` for a single-run input error and `2` for a series input error.
HTTP 401/403 responses remain failed-request measurements and do not abort a
completed collection; inspect the error rate. Warmup responses remain excluded
from measured metrics, including warmup authentication failures.

Saved JSON contains the variable **name**, but never its resolved value.
Credentials are not included in fingerprints or normal CLI error messages.
Comparison loads saved results without reading environment variables, so it
works offline with no credentials present.

Baseline and candidate may use different variable names and token values.
Comparisons require the same authentication mode (Bearer or anonymous), and
fingerprints include that mode while excluding credential source names and
values. Token rotation does not change the workload fingerprint. Anonymous
results keep their existing fingerprints.

Matching authentication modes does not prove equivalent permissions, users,
or tenants. Choose credentials with comparable access and data scope yourself.
Omit `bearer_token_env` (or set it to `null`) for anonymous requests.
