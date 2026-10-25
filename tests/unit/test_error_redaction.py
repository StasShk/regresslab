"""Untrusted validation inputs must not appear in CLI or loader error messages."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.provenance import build_provenance, environment_warnings
from regresslab.core.result_loader import load_saved_result
from regresslab.core.saved_results import validate_experiment_compatibility
from regresslab.core.validation_errors import safe_validation_details

SECRET = "never-print-this-token"


def test_cli_invalid_url_does_not_echo_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text(f"name: api\nurl: http://user:{SECRET}@\nrequests: 2\n")
    assert cli.main(["run", str(source)]) == 1
    error = capsys.readouterr().err
    assert "regresslab: invalid experiment:" in error
    assert "url" in error
    assert SECRET not in error


def test_series_invalid_config_does_not_echo_pydantic_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(f"name: api\nurl: http://user:{SECRET}@\nrequests: 2\n")
    assert (
        cli.main(
            [
                "run-series",
                str(invalid),
                str(invalid),
                "--repeats",
                "1",
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
    assert SECRET not in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_unknown_field_name_and_value_never_appear() -> None:
    with pytest.raises(ValidationError) as captured:
        ExperimentSpec.model_validate(
            {"name": "api", "url": "http://localhost/", "requests": 1, SECRET: SECRET}
        )
    safe = safe_validation_details(captured.value)
    assert "unknown field" in safe
    assert SECRET not in safe


@pytest.mark.parametrize("kind", ["metrics", "measurement", "experiment"])
def test_saved_result_validation_redacts_bad_user_values(
    tmp_path: Path, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    record = {
        "schema_version": 1,
        "experiment": {"name": "api", "url": "http://localhost/test", "requests": 1},
        "metrics": {
            "p50_ms": 1.0,
            "p95_ms": 1.0,
            "p99_ms": 1.0,
            "successful_throughput_rps": 1.0,
            "error_rate": 0.0,
        },
    }
    if kind == "metrics":
        record["metrics"]["p95_ms"] = SECRET
    elif kind == "measurement":
        record["measurement"] = {
            "latencies_ms": [SECRET],
            "request_count": 1,
            "successful_requests": 1,
            "failed_requests": 0,
            "elapsed_seconds": 1.0,
        }
    else:
        record["experiment"]["url"] = f"http://user:{SECRET}@"
    path = tmp_path / "result.json"
    path.write_text(json.dumps(record))
    assert cli.main(["compare", str(path), str(path)]) == 2
    diagnostic = capsys.readouterr().err
    assert "invalid " + kind in diagnostic
    assert SECRET not in diagnostic
    with pytest.raises((TypeError, ValueError)) as exc:
        load_saved_result(path)
    assert SECRET not in str(exc.value)


def test_compatibility_failure_redacts_query_and_names() -> None:
    a = ExperimentSpec(name=SECRET, url=f"http://example.test/path?x={SECRET}", requests=1)
    b = ExperimentSpec(name="other", url="http://example.test/other?x=different", requests=1)
    with pytest.raises(ValueError, match="name differs") as exc:
        validate_experiment_compatibility(a, b)
    assert SECRET not in str(exc.value)
    a2 = ExperimentSpec(name="api", url=f"http://example.test/path?x={SECRET}", requests=1)
    b2 = ExperimentSpec(name="api", url="http://example.test/path?x=different", requests=1)
    with pytest.raises(ValueError, match="URL query differs") as exc2:
        validate_experiment_compatibility(a2, b2)
    assert SECRET not in str(exc2.value)


def test_manifest_errors_hide_injected_field_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "pairs.yaml"
    manifest.write_text(
        f"pairs:\n  - id: first\n    baseline: x.json\n    candidate: x.json\n    {SECRET}: {SECRET}\n"
    )
    assert cli.main(["compare-series", str(manifest)]) == 2
    err = capsys.readouterr().err
    assert "invalid series manifest" in err
    assert SECRET not in err


def test_provenance_validation_error_does_not_include_untrusted_values(tmp_path: Path) -> None:
    specimen = ExperimentSpec(name="api", url="http://localhost/test", requests=1)
    record = {
        "schema_version": 1,
        "experiment": specimen.model_dump(mode="json"),
        "metrics": {
            "p50_ms": 1.0,
            "p95_ms": 1.0,
            "p99_ms": 1.0,
            "successful_throughput_rps": 1.0,
            "error_rate": 0.0,
        },
        **build_provenance(specimen).model_dump(mode="json"),
    }
    record["environment"]["environment_id"] = SECRET + ".example.com"
    path = tmp_path / "saved.json"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="invalid environment provenance") as exc:
        load_saved_result(path)
    assert SECRET not in str(exc.value)


def test_environment_difference_does_not_print_identifiers() -> None:
    a = ExperimentSpec(
        name="api",
        url="http://localhost/test",
        requests=1,
        environment={"environment_id": "private-a"},
    )
    b = ExperimentSpec(
        name="api",
        url="http://localhost/test",
        requests=1,
        environment={"environment_id": "private-b"},
    )
    warnings = environment_warnings(build_provenance(a), build_provenance(b))
    assert "environment.environment_id differs" in warnings[0]
    assert "private-a" not in warnings[0] and "private-b" not in warnings[0]
