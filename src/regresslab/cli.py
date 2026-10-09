"""Command-line interface for running and comparing HTTP benchmarks."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from regresslab.core.compare import (
    ComparisonResult,
    ComparisonStatus,
    MetricComparison,
    RegressionThresholds,
    compare_metrics,
)
from regresslab.core.config import ExperimentSpec, load_experiment
from regresslab.core.http_runner import run_experiment
from regresslab.core.metrics import BenchmarkMetrics, calculate_metrics
from regresslab.core.models import Measurement


def _format_metric(value: float | None, suffix: str, *, scale: float = 1.0) -> str:
    if value is None:
        return "n/a"
    return f"{value * scale:.2f}{suffix}"


def _print_report(
    spec: ExperimentSpec,
    measurement: Measurement,
    metrics: BenchmarkMetrics,
) -> None:
    print(f"Experiment: {spec.name}")
    print(f"Target: {spec.url}")
    print(
        f"Requests: {measurement.request_count} total, "
        f"{measurement.successful_requests} successful, "
        f"{measurement.failed_requests} failed"
    )
    print(f"Elapsed: {measurement.elapsed_seconds:.3f} s")
    print(f"p50: {_format_metric(metrics.p50_ms, ' ms')}")
    print(f"p95: {_format_metric(metrics.p95_ms, ' ms')}")
    print(f"p99: {_format_metric(metrics.p99_ms, ' ms')}")
    print(f"Successful throughput: {_format_metric(metrics.successful_throughput_rps, ' req/s')}")
    print(f"Error rate: {_format_metric(metrics.error_rate, '%', scale=100)}")
    if measurement.failure_detail is not None:
        print(f"Failures: {measurement.failure_detail}")


def _save_result(
    path: Path,
    spec: ExperimentSpec,
    measurement: Measurement,
    metrics: BenchmarkMetrics,
) -> None:
    """Store the inputs and measured values for a future comparison command."""
    result = {
        "schema_version": 1,
        "experiment": spec.model_dump(mode="json"),
        "measurement": measurement.model_dump(mode="json"),
        "metrics": metrics.model_dump(mode="json"),
    }
    path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _load_saved_result(path: Path) -> tuple[ExperimentSpec, BenchmarkMetrics]:
    """Load experiment settings and metrics from a saved benchmark result."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON ({exc.msg})") from exc

    if not isinstance(document, dict):
        raise TypeError(f"{path}: expected a JSON object")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError(f"{path}: unsupported schema_version (expected 1)")
    if not isinstance(document.get("metrics"), dict):
        raise TypeError(f"{path}: missing or invalid metrics object")
    try:
        metrics = BenchmarkMetrics.model_validate(document["metrics"])
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid metrics: {exc}") from exc

    if not isinstance(document.get("experiment"), dict):
        raise TypeError(f"{path}: missing or invalid experiment object")
    try:
        experiment = ExperimentSpec.model_validate(document["experiment"])
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid experiment: {exc}") from exc

    return experiment, metrics


def _validate_experiment_compatibility(
    baseline: ExperimentSpec,
    candidate: ExperimentSpec,
) -> None:
    """Require equivalent workloads, while allowing different hosts and ports."""
    checks = (
        ("name", baseline.name, candidate.name),
        ("URL scheme", baseline.url.scheme, candidate.url.scheme),
        ("URL path", baseline.url.path, candidate.url.path),
        ("URL query", baseline.url.query, candidate.url.query),
        ("requests", baseline.requests, candidate.requests),
        ("concurrency", baseline.concurrency, candidate.concurrency),
    )
    for field, before, after in checks:
        if before != after:
            raise ValueError(
                f"incompatible experiments: {field} differs "
                f"(baseline={before!r}, candidate={after!r})"
            )


def _format_change(comparison: MetricComparison, *, points: bool = False) -> str:
    value = comparison.change_points if points else comparison.change_pct
    if value is None:
        return "n/a"
    return f"{value:+.2f}{' pp' if points else '%'}"


def _print_comparison_report(
    baseline: Path,
    candidate: Path,
    result: ComparisonResult,
    thresholds: RegressionThresholds,
) -> None:
    print(f"Baseline: {baseline}")
    print(f"Candidate: {candidate}")
    print(
        f"{'Metric':<24} {'Baseline':>14} {'Candidate':>14} "
        f"{'Change':>12} {'Threshold':>12}  Result"
    )

    def print_metric(
        label: str,
        comparison: MetricComparison,
        suffix: str,
        threshold: float,
        *,
        points: bool = False,
        scale: float = 1.0,
    ) -> None:
        before = _format_metric(comparison.baseline, suffix, scale=scale)
        after = _format_metric(comparison.candidate, suffix, scale=scale)
        change = _format_change(comparison, points=points)
        limit = f"{threshold:.2f}{' pp' if points else '%'}"
        print(f"{label:<24} {before:>14} {after:>14} {change:>12} {limit:>12}  {comparison.status}")
        if comparison.status is ComparisonStatus.INCONCLUSIVE and comparison.reason:
            print(f"  Reason: {comparison.reason}")

    print_metric("p95 latency", result.p95_latency_ms, " ms", thresholds.p95_latency_pct)
    print_metric(
        "Successful throughput",
        result.successful_throughput_rps,
        " req/s",
        thresholds.throughput_pct,
    )
    print_metric(
        "Error rate",
        result.error_rate,
        "%",
        thresholds.error_rate_points,
        points=True,
        scale=100.0,
    )
    print(f"Overall: {result.status}")


def _compare_saved_results(args: argparse.Namespace) -> int:
    try:
        thresholds = RegressionThresholds(
            p95_latency_pct=args.p95_latency_pct,
            throughput_pct=args.throughput_pct,
            error_rate_points=args.error_rate_points,
        )
        baseline_spec, baseline_metrics = _load_saved_result(args.baseline)
        candidate_spec, candidate_metrics = _load_saved_result(args.candidate)
        _validate_experiment_compatibility(baseline_spec, candidate_spec)
        result = compare_metrics(baseline_metrics, candidate_metrics, thresholds)
    except (OSError, TypeError, ValueError) as exc:
        print(f"regresslab: {exc}", file=sys.stderr)
        return 2

    _print_comparison_report(args.baseline, args.candidate, result, thresholds)
    return {
        ComparisonStatus.PASS: 0,
        ComparisonStatus.REGRESSION: 1,
        ComparisonStatus.INCONCLUSIVE: 3,
    }[result.status]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="regresslab", description="HTTP benchmark runner")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run an HTTP benchmark from a YAML config")
    run.add_argument("config", type=Path, help="Path to the experiment YAML file")
    run.add_argument("--output", type=Path, metavar="FILE", help="Save full result as JSON")
    compare = commands.add_parser(
        "compare",
        help="Compare two saved benchmark JSON results",
        description="Exit codes: 0=PASS, 1=REGRESSION, 2=ERROR, 3=INCONCLUSIVE.",
    )
    compare.add_argument("baseline", type=Path, help="Baseline JSON from run --output")
    compare.add_argument("candidate", type=Path, help="Candidate JSON from run --output")
    compare.add_argument(
        "--p95-latency-pct",
        type=float,
        default=10.0,
        metavar="PCT",
        help="Maximum allowed p95 latency increase (default: 10%%)",
    )
    compare.add_argument(
        "--throughput-pct",
        type=float,
        default=10.0,
        metavar="PCT",
        help="Maximum allowed successful throughput decrease (default: 10%%)",
    )
    compare.add_argument(
        "--error-rate-points",
        type=float,
        default=1.0,
        metavar="POINTS",
        help="Maximum allowed error rate increase in percentage points (default: 1)",
    )
    args = parser.parse_args(argv)

    if args.command == "compare":
        return _compare_saved_results(args)

    try:
        spec = load_experiment(args.config)
        if args.output is not None and args.output.resolve() == args.config.resolve():
            raise ValueError("output path must differ from the experiment config path")

        measurement = asyncio.run(run_experiment(spec))
        metrics = calculate_metrics(measurement)
        if args.output is not None:
            _save_result(args.output, spec, measurement, metrics)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"regresslab: {exc}", file=sys.stderr)
        return 1

    _print_report(spec, measurement, metrics)
    if args.output is not None:
        print(f"Saved JSON: {args.output}")
    return 0
