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
