"""Shared saved-result format and workload compatibility checks."""

import json
from pathlib import Path

from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics
from regresslab.core.models import Measurement


def save_result(
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


def validate_experiment_compatibility(
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
