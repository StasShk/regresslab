"""Command-line interface for running and comparing HTTP benchmarks."""

import argparse
import asyncio
import json
import sys
from contextlib import nullcontext
from pathlib import Path

import yaml

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
from regresslab.core.output_sanitization import safe_failure_summary
from regresslab.core.provenance import environment_warnings
from regresslab.core.result_loader import SavedBenchmarkResult, load_saved_result
from regresslab.core.saved_results import _safe_experiment_document, new_result_file
from regresslab.core.saved_results import (
    save_result as _save_result,
)
from regresslab.core.saved_results import (
    validate_experiment_compatibility as _validate_experiment_compatibility,
)
from regresslab.core.series import (
    BenchmarkPair,
    MetricSeriesSummary,
    SeriesComparison,
    compare_series,
)
from regresslab.core.series_manifest import load_series_manifest
from regresslab.core.series_runner import run_series
from regresslab.core.statistical_gate import StatisticalGateResult, gate_series
from regresslab.core.validation_errors import safe_cli_exception


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
    # The original URL may contain userinfo, private hosts, paths or tokens.
    print(f"Target: {spec.url.scheme}://[redacted]")
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
    summary = safe_failure_summary(measurement.failed_requests, measurement.failure_detail)
    if summary is not None:
        print(f"Failures: {summary}")


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


def _thresholds_from_args(args: argparse.Namespace) -> RegressionThresholds:
    return RegressionThresholds(
        p95_latency_pct=args.p95_latency_pct,
        throughput_pct=args.throughput_pct,
        error_rate_points=args.error_rate_points,
    )


def _load_saved_series(
    manifest_path: Path,
    *,
    require_unique_sources: bool = False,
    strict_environment: bool = False,
) -> tuple[BenchmarkPair, ...]:
    manifest = load_series_manifest(manifest_path)
    reference_spec: ExperimentSpec | None = None
    reference_baseline: SavedBenchmarkResult | None = None
    reference_candidate: SavedBenchmarkResult | None = None
    warnings: set[str] = set()
    pairs: list[BenchmarkPair] = []
    # Repeated inputs are fine for descriptive reports, but not statistical gates.
    # Resolve path aliases and detect hard links to the same underlying file.
    source_owners: dict[Path, str] = {}
    inode_owners: dict[tuple[int, int], str] = {}
    for item in manifest.pairs:
        baseline_path = manifest_path.parent / item.baseline
        candidate_path = manifest_path.parent / item.candidate
        if require_unique_sources:
            for role, path in (("baseline", baseline_path), ("candidate", candidate_path)):
                canonical = path.resolve()
                stat = path.stat()  # Follows symlinks; hard links share device/inode.
                identity = (stat.st_dev, stat.st_ino)
                previous_owner = source_owners.get(canonical)
                if previous_owner is None and stat.st_ino:
                    previous_owner = inode_owners.get(identity)
                if previous_owner is not None:
                    raise ValueError(
                        f"statistical gate requires distinct result files: {path} "
                        f"(pair {item.id!r} {role}) reuses {previous_owner}"
                    )
                owner = f"pair {item.id!r} {role}"
                source_owners[canonical] = owner
                if stat.st_ino:  # An inode of zero may mean the filesystem cannot report one.
                    inode_owners[identity] = owner
        baseline_result = load_saved_result(baseline_path)
        candidate_result = load_saved_result(candidate_path)
        baseline_spec, baseline_metrics = baseline_result.experiment, baseline_result.metrics
        candidate_spec, candidate_metrics = candidate_result.experiment, candidate_result.metrics
        try:
            _validate_experiment_compatibility(baseline_spec, candidate_spec)
            if reference_spec is not None:
                _validate_experiment_compatibility(reference_spec, baseline_spec)
                _validate_experiment_compatibility(reference_spec, candidate_spec)
            warnings.update(
                environment_warnings(
                    baseline_result.provenance,
                    candidate_result.provenance,
                    strict=strict_environment,
                )
            )
            # Check runner consistency within each role across the series.
            if reference_baseline is not None and reference_candidate is not None:
                for first, second in (
                    (reference_baseline, baseline_result),
                    (reference_candidate, candidate_result),
                ):
                    warnings.update(
                        environment_warnings(
                            first.provenance,
                            second.provenance,
                            strict=strict_environment,
                        )
                    )
        except ValueError as exc:
            raise ValueError(f"pair {item.id!r}: {exc}") from exc
        if reference_spec is None:
            reference_spec = baseline_spec
            reference_baseline = baseline_result
            reference_candidate = candidate_result
        pairs.append(
            BenchmarkPair(pair_id=item.id, baseline=baseline_metrics, candidate=candidate_metrics)
        )
    for message in sorted(warnings):
        print(f"regresslab: warning: {message}", file=sys.stderr)
    return tuple(pairs)


def _format_series_change(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    suffix = "%" if unit == "percent" else " pp"
    return f"{value:+.2f}{suffix}"


def _print_series_report(manifest: Path, result: SeriesComparison) -> None:
    print(f"Series manifest: {manifest}")
    print(f"Pairs: {result.pair_count}")
    thresholds = result.thresholds
    print(
        f"Thresholds: p95 +{thresholds.p95_latency_pct:g}%, "
        f"throughput -{thresholds.throughput_pct:g}%, "
        f"error rate +{thresholds.error_rate_points:g} pp"
    )
    width = max(7, *(len(pair.pair_id) for pair in result.pairs))
    print(
        f"{'Pair ID':<{width}}  {'p95 latency':<25}  {'Throughput':<25}  {'Error rate':<25}  Result"
    )
    for pair in result.pairs:
        comparison = pair.comparison
        p95 = f"{_format_change(comparison.p95_latency_ms)} ({comparison.p95_latency_ms.status})"
        throughput = (
            f"{_format_change(comparison.successful_throughput_rps)} "
            f"({comparison.successful_throughput_rps.status})"
        )
        error = (
            f"{_format_change(comparison.error_rate, points=True)} ({comparison.error_rate.status})"
        )
        print(
            f"{pair.pair_id:<{width}}  {p95:<25}  {throughput:<25}  "
            f"{error:<25}  {comparison.status}"
        )

    print("Metric summaries (paired changes; no series-wide verdict):")
    print(
        f"{'Metric':<24} {'PASS':>5} {'REGRESSION':>11} "
        f"{'INCONCLUSIVE':>13} {'Defined':>7}  Median [min, max]"
    )

    def metric_line(name: str, summary: MetricSeriesSummary) -> None:
        median = _format_series_change(summary.median_change, summary.unit)
        lower = _format_series_change(summary.min_change, summary.unit)
        upper = _format_series_change(summary.max_change, summary.unit)
        print(
            f"{name:<24} {summary.pass_count:>5} {summary.regression_count:>11} "
            f"{summary.inconclusive_count:>13} {summary.defined_change_count:>7}  "
            f"{median} [{lower}, {upper}]"
        )

    metric_line("p95 latency", result.p95_latency_ms)
    metric_line("Successful throughput", result.successful_throughput_rps)
    metric_line("Error rate", result.error_rate)


def _print_statistical_gate(result: StatisticalGateResult) -> None:
    print(
        f"Statistical gate: {result.status} "
        f"(median paired worsening; confidence={result.confidence:g}, "
        f"min_pairs={result.min_pairs})"
    )
    for label, metric in (
        ("p95 latency", result.p95_latency_ms),
        ("Successful throughput", result.successful_throughput_rps),
        ("Error rate", result.error_rate),
    ):
        unit = "%" if metric.unit == "percent" else " pp"
        lower = "n/a" if metric.lower_bound is None else f"{metric.lower_bound:+.2f}{unit}"
        upper = "n/a" if metric.upper_bound is None else f"{metric.upper_bound:+.2f}{unit}"
        print(
            f"  {label}: {metric.status} "
            f"(bounds [{lower}, {upper}], limit +{metric.threshold:g}{unit}, "
            f"valid pairs {metric.valid_pairs})"
        )
        if metric.reason:
            print(f"    Reason: {metric.reason}")


def _compare_saved_series(args: argparse.Namespace) -> int:
    try:
        thresholds = _thresholds_from_args(args)
        if not args.gate and (args.min_pairs is not None or args.confidence is not None):
            raise ValueError("--min-pairs and --confidence require --gate")
        pairs = _load_saved_series(
            args.manifest,
            require_unique_sources=args.gate,
            strict_environment=args.strict_environment,
        )
        result = compare_series(pairs, thresholds)
        gate = (
            gate_series(
                pairs,
                thresholds,
                min_pairs=args.min_pairs if args.min_pairs is not None else 8,
                confidence=args.confidence if args.confidence is not None else 0.95,
            )
            if args.gate
            else None
        )
        if args.output is not None:
            document = {"schema_version": 1, "series": result.model_dump(mode="json")}
            if gate is not None:
                document["gate"] = gate.model_dump(mode="json")
            serialized = json.dumps(document, indent=2, allow_nan=False) + "\n"
            # Publish only a complete report, without overwriting a competing writer.
            with new_result_file(args.output) as temporary:
                temporary.write_text(serialized, encoding="utf-8")
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"regresslab: {exc}", file=sys.stderr)
        return 2

    _print_series_report(args.manifest, result)
    if gate is not None:
        _print_statistical_gate(gate)
        return {
            ComparisonStatus.PASS: 0,
            ComparisonStatus.REGRESSION: 1,
            ComparisonStatus.INCONCLUSIVE: 3,
        }[gate.status]
    # Non-gate mode retains its descriptive report and existing exit code.
    return 0


def _add_threshold_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--p95-latency-pct",
        type=float,
        default=10.0,
        metavar="PCT",
        help="Maximum allowed p95 latency increase (default: 10%%)",
    )
    parser.add_argument(
        "--throughput-pct",
        type=float,
        default=10.0,
        metavar="PCT",
        help="Maximum allowed successful throughput decrease (default: 10%%)",
    )
    parser.add_argument(
        "--error-rate-points",
        type=float,
        default=1.0,
        metavar="POINTS",
        help="Maximum allowed error rate increase in percentage points (default: 1)",
    )


def _compare_saved_results(args: argparse.Namespace) -> int:
    try:
        thresholds = _thresholds_from_args(args)
        baseline_result = load_saved_result(args.baseline)
        candidate_result = load_saved_result(args.candidate)
        _validate_experiment_compatibility(baseline_result.experiment, candidate_result.experiment)
        for message in environment_warnings(
            baseline_result.provenance,
            candidate_result.provenance,
            strict=args.strict_environment,
        ):
            print(f"regresslab: warning: {message}", file=sys.stderr)
        result = compare_metrics(baseline_result.metrics, candidate_result.metrics, thresholds)
    except (OSError, TypeError, ValueError) as exc:
        print(f"regresslab: {exc}", file=sys.stderr)
        return 2

    _print_comparison_report(args.baseline, args.candidate, result, thresholds)
    return {
        ComparisonStatus.PASS: 0,
        ComparisonStatus.REGRESSION: 1,
        ComparisonStatus.INCONCLUSIVE: 3,
    }[result.status]


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _run_series_command(args: argparse.Namespace) -> int:
    try:
        baseline = load_experiment(args.baseline)
        candidate = load_experiment(args.candidate)
        manifest = asyncio.run(
            run_series(baseline, candidate, repeats=args.repeats, output_dir=args.output_dir)
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(
            f"regresslab: series interrupted; completed measurements remain in {args.output_dir}",
            file=sys.stderr,
        )
        return 130
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"regresslab: {safe_cli_exception(exc)}", file=sys.stderr)
        return 2

    print(f"Saved series manifest: {manifest} ({args.repeats} pairs)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="regresslab", description="HTTP benchmark runner")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run an HTTP benchmark from a YAML config")
    run.add_argument("config", type=Path, help="Path to the experiment YAML file")
    run.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Save full result as JSON (file must not exist; parent must exist)",
    )
    collect = commands.add_parser(
        "run-series",
        help="Collect matched benchmark pairs in alternating execution order",
        description=(
            "Run sequential baseline/candidate pairs and save a compare-series manifest. "
            "Exit codes: 0=SERIES COLLECTED, 2=ERROR, 130=INTERRUPTED."
        ),
    )
    collect.add_argument("baseline", type=Path, help="Baseline experiment YAML")
    collect.add_argument("candidate", type=Path, help="Candidate experiment YAML")
    collect.add_argument(
        "--repeats", type=_positive_int, required=True, metavar="N", help="Number of matched pairs"
    )
    collect.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        metavar="DIR",
        help="New directory for measurements and pairs.yaml (must not exist)",
    )
    compare = commands.add_parser(
        "compare",
        help="Compare two saved benchmark JSON results",
        description="Exit codes: 0=PASS, 1=REGRESSION, 2=ERROR, 3=INCONCLUSIVE.",
    )
    compare.add_argument("baseline", type=Path, help="Baseline JSON from run --output")
    compare.add_argument("candidate", type=Path, help="Candidate JSON from run --output")
    compare.add_argument(
        "--strict-environment",
        action="store_true",
        help="Reject missing or mismatched testbed provenance",
    )
    _add_threshold_arguments(compare)
    compare_series_command = commands.add_parser(
        "compare-series",
        help="Summarize matched pairs of saved benchmark results",
        description=(
            "Describe per-pair changes; optionally make a statistical decision with --gate. "
            "Without --gate: 0=REPORT GENERATED, 2=ERROR. "
            "With --gate: 0=PASS, 1=REGRESSION, 2=ERROR, 3=INCONCLUSIVE."
        ),
    )
    compare_series_command.add_argument("manifest", type=Path, help="Series YAML manifest")
    compare_series_command.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Save the descriptive series report as JSON (file must not exist)",
    )
    _add_threshold_arguments(compare_series_command)
    compare_series_command.add_argument(
        "--strict-environment",
        action="store_true",
        help="Reject missing or mismatched testbed provenance",
    )
    compare_series_command.add_argument(
        "--gate", action="store_true", help="Enable the statistical series-level decision"
    )
    compare_series_command.add_argument(
        "--min-pairs",
        type=_positive_int,
        metavar="N",
        help="Minimum matched pairs for --gate (default: 8)",
    )
    compare_series_command.add_argument(
        "--confidence",
        type=float,
        metavar="LEVEL",
        help="Familywise statistical confidence for --gate (default: 0.95)",
    )
    args = parser.parse_args(argv)

    if args.command == "compare":
        return _compare_saved_results(args)
    if args.command == "compare-series":
        return _compare_saved_series(args)
    if args.command == "run-series":
        return _run_series_command(args)

    try:
        spec = load_experiment(args.config)
        if args.output is not None:
            _safe_experiment_document(spec)  # Validate before sending HTTP requests.
        if args.output is not None and args.output.resolve() == args.config.resolve():
            raise ValueError("output path must differ from the experiment config path")

        output_context = new_result_file(args.output) if args.output is not None else nullcontext()
        with output_context as temporary:
            measurement = asyncio.run(run_experiment(spec))
            metrics = calculate_metrics(measurement)
            if temporary is not None:
                _save_result(temporary, spec, measurement, metrics)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"regresslab: {safe_cli_exception(exc)}", file=sys.stderr)
        return 1

    _print_report(spec, measurement, metrics)
    if args.output is not None:
        print(f"Saved JSON: {args.output}")
    return 0
