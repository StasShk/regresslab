"""Environment provenance should be reproducible, optional, and safe."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics, calculate_metrics
from regresslab.core.models import Measurement
from regresslab.core.provenance import (
    EnvironmentMetadata,
    build_provenance,
    configuration_fingerprint,
    environment_warnings,
    load_provenance,
)


def spec(url: str = "http://localhost:8000/orders", **fields: object) -> ExperimentSpec:
    return ExperimentSpec.model_validate({"name": "orders", "url": url, "requests": 20, **fields})


def result(path: Path, *, env: dict[str, str] | None = None, legacy: bool = False) -> Path:
    experiment = spec(environment=env) if env is not None else spec()
    measurement = Measurement(
        latencies_ms=(5.0,) * experiment.requests,
        request_count=experiment.requests,
        successful_requests=experiment.requests,
        failed_requests=0,
        elapsed_seconds=experiment.requests / 10,
    )
    record = {
        "schema_version": 1,
        "experiment": experiment.model_dump(mode="json"),
        "measurement": measurement.model_dump(mode="json"),
        "metrics": calculate_metrics(measurement).model_dump(mode="json"),
    }
    if not legacy:
        record.update(build_provenance(experiment).model_dump(mode="json"))
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_fingerprint_excludes_hostname_and_environment_identifiers() -> None:
    baseline = spec(
        "http://localhost:8000/orders?mode=normal",
        environment={"git_sha": "a1b2c3d", "service_revision": "orders:v17"},
    )
    candidate = spec(
        "http://other.example:9000/orders?mode=normal",
        environment={"git_sha": "d4e5f6a", "service_revision": "orders:v18"},
    )
    assert configuration_fingerprint(baseline) == configuration_fingerprint(candidate)
    assert configuration_fingerprint(baseline) != configuration_fingerprint(
        spec("http://other.example:9000/orders?mode=slow")
    )
    assert configuration_fingerprint(baseline) != configuration_fingerprint(
        spec("http://other.example:9000/orders?mode=normal", concurrency=2)
    )


def test_environment_rejects_unknown_and_sensitive_fields() -> None:
    for field, value in (
        ("headers", {"Authorization": "secret"}),
        ("hostname", "private.example.com"),
        ("environment_id", "internal.example.com"),
        ("service_revision", "https://internal.example.com/orders"),
        ("git_sha", "secret-token"),
        ("runner_os", "linux\nAuthorization: secret"),
    ):
        with pytest.raises(ValidationError):
            EnvironmentMetadata.model_validate({field: value})


def test_build_includes_defaults_and_only_explicit_overrides() -> None:
    record = build_provenance(spec(environment={"git_sha": "a1b2c3d"}))
    assert record.environment.git_sha == "a1b2c3d"
    assert record.environment.runner_os
    assert record.environment.runner_arch
    assert record.environment.python_version
    assert record.config_fingerprint.startswith("sha256:")
    assert record.regresslab_version
    assert "environment" not in spec(environment={"git_sha": "a1b2c3d"}).model_dump()


def test_comparison_accepts_revisions_and_rejects_testbed_differences() -> None:
    before = build_provenance(
        spec(environment={"service_revision": "orders:v17", "environment_id": "runner-01"})
    )
    after = build_provenance(
        spec(environment={"service_revision": "orders:v18", "environment_id": "runner-01"})
    )
    assert environment_warnings(before, after, strict=True) == ()
    different = build_provenance(
        spec(environment={"service_revision": "orders:v18", "environment_id": "runner-02"})
    )
    assert "environment.environment_id differs" in environment_warnings(before, different)[0]
    with pytest.raises(ValueError, match="incompatible testbed"):
        environment_warnings(before, different, strict=True)


def test_legacy_results_are_optional_but_strict_mode_requires_provenance(tmp_path: Path) -> None:
    old = result(tmp_path / "old.json", legacy=True)
    new = result(tmp_path / "new.json")
    assert load_provenance(old) is None
    assert load_provenance(new) is not None
    assert cli.main(["compare", str(old), str(old)]) == 0
    assert cli.main(["compare", str(old), str(new)]) == 0
    assert cli.main(["compare", str(old), str(new), "--strict-environment"]) == 2
    assert cli.main(["compare", str(old), str(old), "--strict-environment"]) == 2


def test_strict_cli_blocks_mismatched_runner_but_default_only_warns(tmp_path: Path, capsys) -> None:
    baseline = result(tmp_path / "baseline.json", env={"environment_id": "runner-01"})
    candidate = result(tmp_path / "candidate.json", env={"environment_id": "runner-02"})
    assert cli.main(["compare", str(baseline), str(candidate)]) == 0
    assert "environment.environment_id differs" in capsys.readouterr().err
    assert cli.main(["compare", str(baseline), str(candidate), "--strict-environment"]) == 2
    assert "incompatible testbed" in capsys.readouterr().err


def test_incomplete_or_malformed_provenance_is_rejected(tmp_path: Path) -> None:
    path = result(tmp_path / "result.json")
    document = json.loads(path.read_text())
    del document["config_fingerprint"]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="incomplete environment provenance"):
        load_provenance(path)
    document["config_fingerprint"] = "sha256:invalid"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="invalid environment provenance"):
        load_provenance(path)


def test_series_strict_requires_consistent_runner(tmp_path: Path, capsys) -> None:
    first = result(tmp_path / "b1.json", env={"environment_id": "runner-01"})
    second = result(tmp_path / "c1.json", env={"environment_id": "runner-01"})
    third = result(tmp_path / "b2.json", env={"environment_id": "runner-02"})
    fourth = result(tmp_path / "c2.json", env={"environment_id": "runner-01"})
    manifest = tmp_path / "pairs.yaml"
    manifest.write_text(
        "pairs:\n"
        f"  - id: one\n    baseline: {first.name}\n    candidate: {second.name}\n"
        f"  - id: two\n    baseline: {third.name}\n    candidate: {fourth.name}\n"
    )
    assert cli.main(["compare-series", str(manifest)]) == 0
    assert "environment.environment_id differs" in capsys.readouterr().err
    assert cli.main(["compare-series", str(manifest), "--strict-environment"]) == 2
    assert "incompatible testbed" in capsys.readouterr().err


def test_saved_run_contains_provenance_without_duplicate_environment(tmp_path: Path) -> None:
    from regresslab.core.models import Measurement
    from regresslab.core.saved_results import save_result

    config = spec(
        environment={
            "git_sha": "a1b2c3d",
            "service_revision": "orders:v18",
            "environment_id": "perf-runner-01",
        }
    )
    measurement = Measurement(
        latencies_ms=(5.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    metrics = BenchmarkMetrics(
        p50_ms=5.0,
        p95_ms=5.0,
        p99_ms=5.0,
        successful_throughput_rps=1.0,
        error_rate=0.0,
    )
    target = tmp_path / "result.json"
    save_result(target, config, measurement, metrics)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["environment"]["git_sha"] == "a1b2c3d"
    assert document["environment"]["service_revision"] == "orders:v18"
    assert document["environment"]["environment_id"] == "perf-runner-01"
    assert "environment" not in document["experiment"]
    assert document["config_fingerprint"].startswith("sha256:")
    assert document["regresslab_version"]


def test_saved_result_redacts_target_hostname_and_credentials(tmp_path: Path) -> None:
    from regresslab.core.models import Measurement
    from regresslab.core.saved_results import save_result

    config = spec("http://user:password@private.internal:8000/orders?mode=smoke")
    measurement = Measurement(
        latencies_ms=(5.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    metrics = BenchmarkMetrics(
        p50_ms=5.0,
        p95_ms=5.0,
        p99_ms=5.0,
        successful_throughput_rps=1.0,
        error_rate=0.0,
    )
    output = tmp_path / "result.json"
    save_result(output, config, measurement, metrics)
    contents = output.read_text()
    assert "private.internal" not in contents
    assert "user:password" not in contents
    assert json.loads(contents)["experiment"]["url"] == (
        "http://redacted.invalid/orders?mode=smoke"
    )


def test_sensitive_query_rejected_without_publishing(tmp_path: Path) -> None:
    from regresslab.core.models import Measurement
    from regresslab.core.saved_results import save_result

    config = spec("http://private.internal/orders?api_key=topsecret")
    measurement = Measurement(
        latencies_ms=(5.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    metrics = BenchmarkMetrics(
        p50_ms=5.0,
        p95_ms=5.0,
        p99_ms=5.0,
        successful_throughput_rps=1.0,
        error_rate=0.0,
    )
    output = tmp_path / "result.json"
    with pytest.raises(ValueError, match="sensitive URL query"):
        save_result(output, config, measurement, metrics)
    assert not output.exists()
