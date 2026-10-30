# Characterizing the statistical gate (manual research experiment)

This is a **manual, opt-in experiment**, not a benchmark run in CI. The small
smoke test runs in pytest, but the full experiment takes substantially longer.
No changes to the statistical algorithm or runtime dependencies are required.

Run from the repository root with RegressLab installed (`uv sync`):

```sh
uv run python -m experiments.gate_characterization \
  --output-dir results/gate-characterization-001 \
  --trials 10 --pairs 8,12,20,40 \
  --requests 20 --delay-ms 10 --noise 0,10 \
  --effects 0,5,10,25 --seed 12345
```

Use a **new output directory** for each experiment. If your shell configures
an HTTP proxy, set `NO_PROXY=127.0.0.1,localhost` so loopback traffic stays
local. For a faster end-to-end smoke run:

```sh
uv run python -m experiments.gate_characterization \
  --output-dir results/gate-smoke \
  --trials 1 --pairs 8,12 --requests 5 \
  --delay-ms 2 --noise 0 --effects 0,25
```

## Experimental design

- Two independent loopback HTTP servers return HTTP 200 for `GET /health`.
  The baseline sleeps approximately 10 ms; the candidate's *configured server
  sleep* increases by 0% (A/A), 5%, 10%, or 25% (A/B). This is **not** a promise
  that observed p95 latency or throughput will change by exactly that amount.
- Each side adds independent, seeded uniform jitter of ±0% or ±10% **of the
  baseline delay**, as requested by `--noise`. This controls injected noise,
  not operating-system scheduling or the HTTP client overhead.
- `concurrency: 1` avoids artificial contention and makes the controlled
  service delay visible in both latency and throughput. The generator uses the
  real RegressLab `run-series` CLI, including alternating baseline/candidate
  order. Scenario order is shuffled deterministically per trial to reduce
  time-of-day drift confounding; each trial restarts both servers with new seeds.
- A trial collects a maximum-length series (40 pairs by default) once. The
  same run's **nested prefixes** (8, 12, 20, 40) are evaluated by the real
  `compare-series --gate` CLI. This is not four independent trials: outcomes
  for different prefix sizes are correlated. Trials are separate collections.
- Repeating each condition `--trials N` times yields the observed proportions
  of PASS, REGRESSION, and INCONCLUSIVE at each sample size. Never interpret a
  single PASS or REGRESSION as an estimated detection probability.

## Outputs and interpretation

`summary.csv` and `summary.json` contain counts and fractions (0–1) of the
three gate outcomes over trials, grouped by noise, injected effect and number
of pairs. `trials.csv` contains every decision and its seed. Each trial keeps
baseline/candidate YAML, raw saved measurements, `pairs.yaml`, nested-prefix
manifests and the gate JSON reports. `settings.json` records all parameters.
A nonzero error exit from RegressLab is treated as **experiment failure**,
never silently counted as INCONCLUSIVE. A stopped experiment may leave useful
partial raw data without summary files.

The current defaults are `p95_latency_pct=10`, `throughput_pct=10`,
`error_rate_points=1`, confidence `0.95` and `min_pairs=8`. With these
thresholds, 5% injected slowdown is *within tolerance* and is not necessarily
supposed to trigger REGRESSION. A 10% slowdown sits near the boundary;
INCONCLUSIVE may be appropriate. The 25% experiment tests detection when the
signal should exceed the tolerance. In A/A, inspect the false-REGRESSION rate.

Evaluate sensitivity separately from correctness: a high INCONCLUSIVE rate is
not a false positive, and an algorithm cannot guarantee PASS on every A/A run.
Do not adjust default thresholds until measurements from several repeated trials
support a change. Keep reports from each run for comparison.

**Limits:** synthetic sleeps are not production latency distributions. One
local machine shares resources with the generator; CPU load, scheduling, clock
resolution, HTTP overhead and background processes cause uncontrolled noise.
Seeds reproduce injected jitter only, not wall-clock timing. These trials
are not a proof of statistical calibration under arbitrary dependent,
non-stationary workloads. The gate's independence and comparable-pairs
assumptions still apply; test on real services before treating its decisions
as production guarantees.
