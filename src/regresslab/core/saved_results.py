"""Shared saved-result format and workload compatibility checks."""

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl

from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics
from regresslab.core.models import Measurement
from regresslab.core.output_sanitization import safe_failure_summary
from regresslab.core.provenance import build_provenance


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


def _safe_experiment_document(spec: ExperimentSpec) -> dict[str, object]:
    """Redact target host, port, userinfo and fragment in persisted results."""
    query = spec.url.query
    if query:
        for key, _ in parse_qsl(query, keep_blank_values=True):
            if re.search(
                r"(?i)(token|secret|auth|pass|pwd|session|cookie|"
                r"credential|api[_-]?key|signature|key$)",
                key,
            ):
                raise ValueError("cannot save result with sensitive URL query parameters")
    document: dict[str, object] = spec.model_dump(mode="json", exclude={"environment"})
    url = f"{spec.url.scheme}://redacted.invalid{spec.url.path or '/'}"
    if query:
        url += f"?{query}"
    document["url"] = url
    return document


def save_result(
    path: Path,
    spec: ExperimentSpec,
    measurement: Measurement,
    metrics: BenchmarkMetrics,
) -> None:
    """Store the inputs and measured values for a future comparison command."""
    # Protect callers supplying their own Measurement, not just the HTTP runner.
    # Never persist an arbitrary exception message or URL in failure_detail.
    measurement_document = measurement.model_dump(mode="json")
    measurement_document["failure_detail"] = safe_failure_summary(
        measurement.failed_requests, measurement.failure_detail
    )
    result = {
        "schema_version": 1,
        "experiment": _safe_experiment_document(spec),
        "measurement": measurement_document,
        "metrics": metrics.model_dump(mode="json"),
        **build_provenance(spec).model_dump(mode="json"),
    }
    serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if spec.max_result_bytes is not None:
        actual_bytes = len(serialized.encode("utf-8"))
        if actual_bytes > spec.max_result_bytes:
            raise ValueError(
                f"Saved result exceeds max_result_bytes={spec.max_result_bytes}: "
                f"{actual_bytes} bytes"
            )
    path.write_text(serialized, encoding="utf-8")


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
        ("timeout_seconds", baseline.timeout_seconds, candidate.timeout_seconds),
        ("warmup_requests", baseline.warmup_requests, candidate.warmup_requests),
        ("max_duration_seconds", baseline.max_duration_seconds, candidate.max_duration_seconds),
    )
    for field, before, after in checks:
        if before != after:
            # Configuration values can include private URLs, paths and identifiers.
            raise ValueError(f"incompatible experiments: {field} differs")
