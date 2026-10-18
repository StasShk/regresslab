# RegressLab

RegressLab is a tool for detecting performance regressions in software systems.

The project is currently under development.

## Compare saved benchmark results

Save benchmark runs with `regresslab run experiment.yaml --output run.json`,
then compare a pair with `regresslab compare baseline.json candidate.json`.
To summarize several matched pairs from a YAML manifest **without writing Python**:

```sh
regresslab compare-series pairs.yaml --output comparison.json
```

See [the series CLI guide](docs/series-cli.md) for the manifest format, examples,
compatibility rules, and exit-code semantics.
