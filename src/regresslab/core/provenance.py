"""Optional, allowlisted run provenance and environment compatibility.

Never inspect environment variables, HTTP headers, or the machine hostname.
Service revisions and runner IDs must be explicitly supplied as safe identifiers.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from regresslab.core.config import ExperimentSpec


class EnvironmentMetadata(BaseModel):
    """Strict allowlist; never accept arbitrary metadata or raw hostnames."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    git_sha: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{7,64}$")
    # Namespace the revision to avoid accidentally storing a bare hostname.
    service_revision: str | None = Field(
        default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]{1,48}:[A-Za-z0-9_.+-]{1,79}$"
    )
    runner_os: str | None = Field(default=None, max_length=32, pattern=r"^[a-z0-9_-]+$")
    runner_arch: str | None = Field(default=None, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    python_version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+(\.[0-9]+)?$")
    environment_id: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )


class ResultProvenance(BaseModel):
    """Optional top-level fields in schema_version=1 benchmark JSON."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    environment: EnvironmentMetadata
    config_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    regresslab_version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.+!-]+$")


def configuration_fingerprint(spec: ExperimentSpec) -> str:
    """Stable SHA-256 of effective config, excluding endpoint host, port and userinfo.

    The host is intentionally excluded because baseline and candidate can be
    served at different addresses. Path and query are included in the digest
    for workload identity, but are never placed in provenance as plain text.
    """
    config = spec.model_dump(mode="json", exclude={"environment"})
    config["url"] = {
        "scheme": spec.url.scheme,
        "path": spec.url.path,
        "query": spec.url.query,
    }
    canonical = json.dumps(
        {"fingerprint_version": 1, "config": config},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def build_provenance(spec: ExperimentSpec) -> ResultProvenance:
    """Collect non-identifying defaults, then apply approved config overrides."""
    values: dict[str, str] = {
        "runner_os": platform.system().lower(),
        "runner_arch": platform.machine(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    if spec.environment is not None:
        values.update(spec.environment.model_dump(exclude_none=True))

    try:
        package_version = version("regresslab")
    except PackageNotFoundError:
        # Source-tree execution without installation; does not invent a release.
        package_version = "uninstalled"

    return ResultProvenance(
        environment=EnvironmentMetadata.model_validate(values),
        config_fingerprint=configuration_fingerprint(spec),
        regresslab_version=package_version,
    )


def load_provenance(
    path: Path, *, document: dict[str, object] | None = None
) -> ResultProvenance | None:
    """Read provenance; a validated document avoids a second file read."""
    if document is None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON ({exc.msg})") from exc
    if not isinstance(document, dict):
        raise TypeError(f"{path}: expected a JSON object")
    keys = {"environment", "config_fingerprint", "regresslab_version"}
    present = keys.intersection(document)
    if not present:
        return None
    if present != keys:
        raise ValueError(f"{path}: incomplete environment provenance")
    try:
        return ResultProvenance.model_validate({key: document[key] for key in keys})
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid environment provenance: {exc}") from exc


def environment_warnings(
    baseline: ResultProvenance | None,
    candidate: ResultProvenance | None,
    *,
    strict: bool = False,
) -> tuple[str, ...]:
    """Warn about testbed differences, or reject them in strict mode.

    git_sha and service_revision are intentionally *not* equivalence checks:
    measuring a new revision is the point of a regression comparison.
    """
    if baseline is None or candidate is None:
        if strict:
            raise ValueError("strict environment check requires provenance in both results")
        if baseline is None and candidate is None:
            return ()  # Preserve old behavior when comparing two legacy files.
        return ("environment provenance missing from one result",)

    differences: list[str] = []
    for field in ("runner_os", "runner_arch", "python_version", "environment_id"):
        before = getattr(baseline.environment, field)
        after = getattr(candidate.environment, field)
        if before != after:
            differences.append(
                f"environment.{field} differs (baseline={before!r}, candidate={after!r})"
            )
    if baseline.config_fingerprint != candidate.config_fingerprint:
        differences.append("config_fingerprint differs")
    if baseline.regresslab_version != candidate.regresslab_version:
        differences.append(
            "regresslab_version differs "
            f"(baseline={baseline.regresslab_version!r}, "
            f"candidate={candidate.regresslab_version!r})"
        )
    if strict and differences:
        raise ValueError("incompatible testbed: " + "; ".join(differences))
    return tuple(differences)
