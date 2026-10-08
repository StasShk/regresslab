"""Command-line interface for running HTTP benchmark experiments."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="regresslab", description="HTTP benchmark runner")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run an HTTP benchmark from a YAML config")
    run.add_argument("config", type=Path, help="Path to the experiment YAML file")
    run.add_argument("--output", type=Path, metavar="FILE", help="Save full result as JSON")
    args = parser.parse_args(argv)

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
