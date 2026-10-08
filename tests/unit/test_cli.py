"""Tests for the public benchmark CLI."""

import json
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.models import Measurement


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    config = tmp_path / "experiment.yaml"
    config.write_text(
        "name: sample\nurl: http://localhost:8000/health\nrequests: 4\nconcurrency: 2\n",
        encoding="utf-8",
    )
    return config


def install_fake_runner(monkeypatch: pytest.MonkeyPatch, measurement: Measurement) -> None:
    async def fake_run_experiment(spec: ExperimentSpec) -> Measurement:
        assert spec.name == "sample"
        assert spec.requests == 4
        assert spec.concurrency == 2
        return measurement

    monkeypatch.setattr(cli, "run_experiment", fake_run_experiment)


def test_run_displays_metrics_and_saves_json(
    config_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_fake_runner(
        monkeypatch,
        Measurement(
            latencies_ms=(10.0, 20.0, 30.0),
            request_count=4,
            successful_requests=3,
            failed_requests=1,
            elapsed_seconds=0.5,
            failure_detail="1 requests failed; first error: HTTP 500",
        ),
    )
    output = tmp_path / "results.json"

    exit_code = cli.main(["run", str(config_file), "--output", str(output)])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Experiment: sample" in stdout
    assert "3 successful, 1 failed" in stdout
    assert "p95: 30.00 ms" in stdout
    assert "Successful throughput: 6.00 req/s" in stdout
    assert "Error rate: 25.00%" in stdout
    assert "Failures: 1 requests failed" in stdout
    assert f"Saved JSON: {output}" in stdout

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["experiment"]["name"] == "sample"
    assert saved["experiment"]["url"] == "http://localhost:8000/health"
    assert saved["experiment"]["requests"] == 4
    assert saved["measurement"]["latencies_ms"] == [10.0, 20.0, 30.0]
    assert saved["measurement"]["successful_requests"] == 3
    assert saved["metrics"]["p95_ms"] == 30.0
    assert saved["metrics"]["successful_throughput_rps"] == 6.0
    assert saved["metrics"]["error_rate"] == 0.25


def test_run_without_output_is_allowed(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_runner(
        monkeypatch,
        Measurement(
            latencies_ms=(10.0, 10.0, 10.0, 10.0),
            request_count=4,
            successful_requests=4,
            failed_requests=0,
            elapsed_seconds=1.0,
        ),
    )

    assert cli.main(["run", str(config_file)]) == 0
    stdout = capsys.readouterr().out
    assert "p50: 10.00 ms" in stdout
    assert "Error rate: 0.00%" in stdout
    assert "Saved JSON" not in stdout


def test_all_failed_requests_display_na_for_percentiles(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_runner(
        monkeypatch,
        Measurement(
            latencies_ms=(),
            request_count=4,
            successful_requests=0,
            failed_requests=4,
            elapsed_seconds=1.0,
            failure_detail="4 requests failed; first error: HTTP 500",
        ),
    )

    assert cli.main(["run", str(config_file)]) == 0
    stdout = capsys.readouterr().out
    assert "p95: n/a" in stdout
    assert "Successful throughput: 0.00 req/s" in stdout
    assert "Error rate: 100.00%" in stdout


def test_missing_config_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["run", str(tmp_path / "missing.yaml")])

    assert exit_code == 1
    assert "missing.yaml" in capsys.readouterr().err


@pytest.mark.parametrize("content", ["requests: [\n", "requests: 0\n", "- one\n"])
def test_invalid_config_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text(content, encoding="utf-8")

    assert cli.main(["run", str(config)]) == 1
    assert "regresslab:" in capsys.readouterr().err


def test_output_write_error_returns_error(
    config_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_fake_runner(
        monkeypatch,
        Measurement(
            latencies_ms=(10.0, 10.0, 10.0, 10.0),
            request_count=4,
            successful_requests=4,
            failed_requests=0,
            elapsed_seconds=1.0,
        ),
    )

    assert cli.main(["run", str(config_file), "--output", str(tmp_path)]) == 1
    assert "regresslab:" in capsys.readouterr().err


def test_output_cannot_overwrite_config(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = config_file.read_text(encoding="utf-8")

    async def unexpected_runner(spec: ExperimentSpec) -> Measurement:
        raise AssertionError("runner should not execute")

    monkeypatch.setattr(cli, "run_experiment", unexpected_runner)
    assert cli.main(["run", str(config_file), "--output", str(config_file)]) == 1
    assert "output path must differ" in capsys.readouterr().err
    assert config_file.read_text(encoding="utf-8") == original


def test_help_and_missing_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as help_result:
        cli.main(["--help"])
    assert help_result.value.code == 0
    assert "run" in capsys.readouterr().out

    with pytest.raises(SystemExit) as missing_command:
        cli.main([])
    assert missing_command.value.code == 2
