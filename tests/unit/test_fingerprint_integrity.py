"""Saved fingerprints must agree with the saved experiment (current v2 only)."""

import json
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.provenance import build_provenance, configuration_fingerprint
from regresslab.core.result_loader import load_saved_result


def _spec(**overrides: object) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "name": "orders",
            "url": "http://localhost:8000/orders?mode=normal",
            "requests": 20,
            "concurrency": 2,
            "max_requests": 20,
            "max_concurrency": 2,
            "max_result_bytes": 1_000_000,
            **overrides,
        }
    )


def _record(spec: ExperimentSpec) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment": spec.model_dump(mode="json"),
        "metrics": {
            "p50_ms": 5.0,
            "p95_ms": 5.0,
            "p99_ms": 5.0,
            "successful_throughput_rps": 10.0,
            "error_rate": 0.0,
        },
        **build_provenance(spec).model_dump(mode="json"),
    }


def _write(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_valid_fingerprint_loads_and_safety_caps_are_excluded(tmp_path: Path) -> None:
    original = _spec()
    document = _record(original)
    assert load_saved_result(_write(tmp_path / "valid.json", document)).provenance
    experiment = document["experiment"]
    assert isinstance(experiment, dict)
    experiment.update(max_requests=100, max_concurrency=50, max_result_bytes=2_000_000)
    assert load_saved_result(_write(tmp_path / "different-caps.json", document)).provenance
    assert configuration_fingerprint(original) == configuration_fingerprint(
        _spec(max_requests=100, max_concurrency=50, max_result_bytes=2_000_000)
    )


def test_mismatched_sha256_is_rejected_with_and_without_strict_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = _record(_spec())
    document["config_fingerprint"] = "sha256:" + "a" * 64
    damaged = _write(tmp_path / "damaged.json", document)
    with pytest.raises(ValueError, match="config_fingerprint differs"):
        load_saved_result(damaged)
    assert cli.main(["compare", str(damaged), str(damaged)]) == 2
    assert "config_fingerprint differs" in capsys.readouterr().err
    assert cli.main(["compare", str(damaged), str(damaged), "--strict-environment"]) == 2


def test_changed_workload_rejected_but_changed_host_is_allowed(tmp_path: Path) -> None:
    document = _record(_spec())
    experiment = document["experiment"]
    assert isinstance(experiment, dict)
    experiment["url"] = "http://different.internal:9000/orders?mode=normal"
    assert load_saved_result(_write(tmp_path / "host.json", document)).provenance
    experiment["url"] = "http://different.internal:9000/orders?mode=other"
    with pytest.raises(ValueError, match="config_fingerprint differs"):
        load_saved_result(_write(tmp_path / "changed.json", document))


def test_result_without_provenance_remains_supported(tmp_path: Path) -> None:
    document = _record(_spec())
    for key in ("environment", "regresslab_version", "config_fingerprint"):
        del document[key]
    assert load_saved_result(_write(tmp_path / "no-provenance.json", document)).provenance is None
