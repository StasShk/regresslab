"""Shared saved-result format and workload compatibility checks."""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics
from regresslab.core.models import Measurement


@contextmanager
def new_result_file(path: Path) -> Iterator[Path]:
    """Stage a result and publish it atomically without replacing another file.

    Enter this context before starting the benchmark. The parent must exist and
    support hard links. The yielded path is private temporary storage; it is
    published only after the body completes successfully.
    """
    path = path.absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Output path already exists: {path}")

    with TemporaryDirectory(prefix=".regresslab-", dir=path.parent) as directory:
        temporary = Path(directory) / "result.json"
        temporary.touch()
        # Check write access and hard-link support before sending HTTP requests.
        probe = Path(directory) / "probe"
        os.link(temporary, probe)
        probe.unlink()
        yield temporary
        # Unlike replace(), link() fails if another process created the target
        # during measurement. Readers see only the fully written result.
        os.link(temporary, path)


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
