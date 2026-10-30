# Statistical gate: controlled loopback characterization

This document records one **observed** characterization run, not a guarantee
about other machines, applications, or CI environments. The raw `gate-final/`
results were analyzed separately from the library and are not required for
normal RegressLab operation.

## Setup and reproducibility

- Baseline: local HTTP GET endpoint with a **50 ms** server-side sleep.
- Candidate: the same endpoint with **0%, 5%, 10%, or 25%** more server-side
  sleep. These are **configured server delays**, not measured p95 changes.
- Each endpoint runs on `127.0.0.1`; requests use the real RegressLab HTTP runner.
- Noise: independent, seeded, uniformly sampled per-request delay jitter.
  The `0` and `10` conditions represent zero added jitter and ±10% of the
  **baseline delay**, respectively (equal absolute jitter ranges on both sides).
- `requests=20`, `concurrency=1`, `warmup_requests=3` on both sides.
- Ten separately collected trials per condition, 40 alternating baseline/candidate
  pairs per trial. Gate results at 8, 12, 20, and 40 pairs use **nested prefixes**
  of each 40-pair trial, not independent datasets.
- Existing defaults: p95 latency +10%, successful throughput −10%, error rate
  +1 percentage point, 95% familywise confidence, minimum 8 pairs.
- Linux x86_64, Python 3.12, RegressLab 0.1.0 (saved result provenance).

From the repository root (with the characterization script configured to use
`warmup_requests=3` and TCP_NODELAY in its local test HTTP server):

```sh
uv run python -m experiments.gate_characterization \
  --output-dir results/gate-final-reproduce \
  --trials 10 \
  --pairs 8,12,20,40 \
  --requests 20 \
  --delay-ms 50 \
  --noise 0,10 \
  --effects 0,5,10,25 \
  --seed 12345
```

Changing the machine, scheduling load, test-server implementation or runner
can change the measured results. A random seed controls only the scripted
jitter and trial ordering, not the OS scheduler.

## Observed decisions

Each cell is `PASS / REGRESSION / INCONCLUSIVE`, **out of 10 trials**.
For example, `9/0/1` means nine PASS, zero REGRESSION and one INCONCLUSIVE.

| Added jitter | Server delay change | 8 pairs | 12 pairs | 20 pairs | 40 pairs |
|---|---:|---:|---:|---:|---:|
| ±0% | +0% | 10/0/0 | 10/0/0 | 10/0/0 | 10/0/0 |
| ±0% | +5% | 9/0/1 | 9/0/1 | 10/0/0 | 10/0/0 |
| ±0% | +10% | 1/0/9 | 2/0/8 | 7/0/3 | 9/0/1 |
| ±0% | +25% | 0/10/0 | 0/10/0 | 0/10/0 | 0/10/0 |
| ±10% | +0% | 9/0/1 | 10/0/0 | 10/0/0 | 10/0/0 |
| ±10% | +5% | 9/0/1 | 9/0/1 | 10/0/0 | 10/0/0 |
| ±10% | +10% | 1/0/9 | 2/0/8 | 6/0/4 | 8/0/2 |
| ±10% | +25% | 0/9/1 | 0/10/0 | 0/10/0 | 0/10/0 |

All **320 gate reports** reconciled with `summary.json` (8 conditions × 10
collected trials × 4 nested prefix sizes). The archive contains **6,400 saved
benchmark results**, each with 20 measured HTTP requests and 3 configured
warmup requests; there were **no recorded HTTP request failures**.

## Interpretation

- **A/A (0%):** At 20 or 40 pairs, all 20 trials across the two noise
  settings returned PASS. No false REGRESSION appeared in this dataset.
  At 8 pairs, one of 20 trials returned INCONCLUSIVE.
- **+5% server delay:** At 20 and 40 pairs, all 20 trials returned PASS.
  This is expected under the configured +10% p95 / −10% throughput thresholds.
- **+10% server delay:** At 40 pairs, 17 of 20 trials returned PASS and
  three returned INCONCLUSIVE; none returned REGRESSION. Across the trials,
  median-of-trial-median *measured* p95 worsening was about 8–9%, i.e. BELOW
  the gate’s 10% measured-p95 threshold. PASS here is not a false negative
  for the defined gate decision.
- **+25% server delay:** At 8 pairs, 19 of 20 trials returned REGRESSION
  and one was INCONCLUSIVE. At 12, 20 and 40 pairs, all 20 trials returned
  REGRESSION. Median-of-trial-median measured p95 worsening at 40 pairs
  was about 20.8–21.3%; the corresponding throughput worsening was
  about 18.0%. Those are end-to-end measurements, not the full +25%
  server sleep increase.

**Why 8 pairs can be inconclusive:** The gate uses exact median order
statistics with Bonferroni allocation across six one-sided bounds. At 95%
confidence and eight pairs, each metric’s finite bounds are the minimum and
maximum paired worsenings. This is conservative: a single noisy pair can
prevent a conclusive result, even when the median is clearly on one side.

## Conclusions and limits

1. The existing algorithm and defaults gave sensible outcomes for this
   **controlled, warmed-up loopback workload**, without changing the gate.
2. Keep `min_pairs=8` as a **collection floor**, not a promise of sufficient
   power or a universal CI recommendation. Consider more pairs when results
   are close to the threshold or are often INCONCLUSIVE.
3. Interpret configured server delay changes separately from the **measured**
   p95/throughput changes that gate actually evaluates.
4. Use warmup and comparable baseline/candidate conditions; investigate
   environment drift, dependencies and noise before trusting CI verdicts.
5. Ten trials per condition are too few to establish a precise false-positive
   or detection probability. Zero observed errors does **not** imply a zero
   real-world error rate. The prefix-size results are correlated and must not
   be counted as independent replications.
6. This study uses one localhost synthetic service and two configured jitter
   levels; it does not validate performance across real networks, applications,
   concurrent production workloads, or machines with different scheduling.
7. The simultaneous confidence guarantee depends on assumptions about the
   independence and comparability of the **run pairs**; this experiment does
   not prove those assumptions for production workloads.

**Recommended status:** The characterization step is complete for the
controlled workload. Do not change the statistical algorithm based on these
results alone. Further, more representative workload tests are optional
follow-up work, not a prerequisite for documenting this study.

### Evidence

- Input archive supplied for this study: `gate-final.zip`.
- `gate-final/settings.json`: requested experiment configuration.
- `gate-final/summary.json`, `gate-final/summary.csv`: 32 condition/size
  aggregates (counts out of 10).
- `gate-final/trials.csv`: per-trial decisions.
- `gate-final/noise-*/effect-*/trial-*/results/gate-*.json`: individual
  gate metrics and confidence bounds.
- `gate-final/noise-*/effect-*/trial-*/results/pair-*.json`: raw saved
  measurements and p95/throughput/error metrics.
