"""Single trusted reader for saved benchmark results.

Metrics-only schema v1 records are supported for descriptive comparisons. When
raw measurements are available, derived metrics must match the saved values.
"""

import json
from dataclasses import dataclass
from math import isclose, isfinite
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics, calculate_metrics
from regresslab.core.models import Measurement
from regresslab.core.provenance import ResultProvenance, load_provenance
from regresslab.core.validation_errors import safe_validation_details

# A tiny absolute tolerance covers zero/round-off without permitting meaningful
# drift at typical benchmark scales. Finite values and nulls are checked first.
_REL_TOL = 1e-9
_ABS_TOL = 1e-12
_METRIC_FIELDS = (
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "successful_throughput_rps",
    "error_rate",
)


@dataclass(frozen=True, slots=True)
class SavedBenchmarkResult:
    experiment: ExperimentSpec
    metrics: BenchmarkMetrics
    measurement: Measurement | None
    provenance: ResultProvenance | None


def _validate_measurement(path: Path, raw: object) -> Measurement:
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: invalid measurement: expected a JSON object")
    # JSON encodes tuples as lists. Convert *only* that container so strict
    # validation rejects string/bool counters and nonnumeric latency samples.
    data: dict[str, Any] = dict(raw)
    if isinstance(data.get("latencies_ms"), list):
        data["latencies_ms"] = tuple(data["latencies_ms"])
    try:
        return Measurement.model_validate(data, strict=True)
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid measurement: {safe_validation_details(exc)}") from exc


def _matching_value(expected: float | None, actual: float | None) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    return (
        isfinite(expected)
        and isfinite(actual)
        and isclose(expected, actual, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    )


def _verify_derived_metrics(
    path: Path, measurement: Measurement, metrics: BenchmarkMetrics
) -> None:
    try:
        calculated = calculate_metrics(measurement)
    except (ValueError, OverflowError, ValidationError) as exc:
        raise ValueError(f"{path}: invalid measurement metrics: {exc}") from exc
    for field in _METRIC_FIELDS:
        expected = getattr(calculated, field)
        saved = getattr(metrics, field)
        if not _matching_value(expected, saved):
            raise ValueError(
                f"{path}: inconsistent saved result: metrics.{field} "
                f"(calculated={expected!r}, saved={saved!r})"
            )


def load_saved_result(path: Path) -> SavedBenchmarkResult:
    """Read once, validate schema, counters, derived metrics, and provenance.

    A missing measurement means a legacy metrics-only result. A present but
    invalid/null measurement is corruption, never an implicit legacy fallback.
    """
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
        metrics = BenchmarkMetrics.model_validate(document["metrics"], strict=True)
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid metrics: {safe_validation_details(exc)}") from exc

    if not isinstance(document.get("experiment"), dict):
        raise TypeError(f"{path}: missing or invalid experiment object")
    try:
        experiment = ExperimentSpec.model_validate(document["experiment"])
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid experiment: {safe_validation_details(exc)}") from exc

    measurement: Measurement | None = None
    if "measurement" in document:
        measurement = _validate_measurement(path, document["measurement"])
        if measurement.request_count != experiment.requests:
            raise ValueError(
                f"{path}: inconsistent saved result: measurement.request_count "
                f"({measurement.request_count}) differs from experiment.requests "
                f"({experiment.requests})"
            )
        _verify_derived_metrics(path, measurement, metrics)

    provenance = load_provenance(path, document=document)
    return SavedBenchmarkResult(
        experiment=experiment,
        metrics=metrics,
        measurement=measurement,
        provenance=provenance,
    )
