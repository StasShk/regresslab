"""CLI tests for comparing previously saved benchmark results."""

import json
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics, calculate_metrics
from regresslab.core.models import Measurement


def write_result(
    path: Path,
    *,
    p95: float | None = 35.2,
    throughput: float | None = 100.0,
    error_rate: float | None = 0.02,
) -> Path:
    values = BenchmarkMetrics(
        p50_ms=p95,
        p95_ms=p95,
        p99_ms=p95,
        successful_throughput_rps=throughput,
        error_rate=error_rate,
    )
    path.write_text(
        json.dumps({"schema_version": 1, "metrics": values.model_dump(mode="json")}),
        encoding="utf-8",
    )
    return path


def compare_files(baseline: Path, candidate: Path, *options: str) -> int:
    return cli.main(["compare", str(baseline), str(candidate), *options])


def test_equal_results_pass_and_display_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = write_result(tmp_path / "baseline.json")
    candidate = write_result(tmp_path / "candidate.json")

    assert compare_files(baseline, candidate) == 0
    report = capsys.readouterr().out
    assert f"Baseline: {baseline}" in report
    assert f"Candidate: {candidate}" in report
    assert "p95 latency" in report
    assert "Successful throughput" in report
    assert "Error rate" in report
    assert "Overall: PASS" in report


def test_detects_latency_regression(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = write_result(tmp_path / "baseline.json")
    candidate = write_result(tmp_path / "candidate.json", p95=42.6)

    assert compare_files(baseline, candidate) == 1
    output = capsys.readouterr().out
    assert "+21.02%" in output
    assert "Overall: REGRESSION" in output


def test_threshold_override_can_allow_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = write_result(tmp_path / "baseline.json")
    candidate = write_result(tmp_path / "candidate.json", p95=42.6)

    assert compare_files(baseline, candidate, "--p95-latency-pct", "25") == 0
    assert "Overall: PASS" in capsys.readouterr().out


def test_throughput_and_error_thresholds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = write_result(tmp_path / "baseline.json")
    candidate = write_result(tmp_path / "candidate.json", throughput=88, error_rate=0.04)

    assert compare_files(baseline, candidate) == 1
    output = capsys.readouterr().out
    assert "-12.00%" in output
    assert "+2.00 pp" in output
    assert "Overall: REGRESSION" in output

    assert (
        compare_files(baseline, candidate, "--throughput-pct", "15", "--error-rate-points", "3")
        == 0
    )
    assert "Overall: PASS" in capsys.readouterr().out


def test_missing_metrics_are_inconclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = write_result(tmp_path / "baseline.json", p95=None, throughput=0, error_rate=1)
    candidate = write_result(tmp_path / "candidate.json", p95=None, throughput=0, error_rate=1)

    assert compare_files(baseline, candidate) == 3
    output = capsys.readouterr().out
    assert "p95 latency" in output
    assert "n/a" in output
    assert "Reason:" in output
    assert "Overall: INCONCLUSIVE" in output


@pytest.mark.parametrize("invalid", ["-0.1", "inf", "nan", "101"])
def test_invalid_thresholds_return_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    baseline = write_result(tmp_path / "baseline.json")
    candidate = write_result(tmp_path / "candidate.json")

    assert compare_files(baseline, candidate, "--error-rate-points", invalid) == 2
    assert "regresslab:" in capsys.readouterr().err


def test_missing_file_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    candidate = write_result(tmp_path / "candidate.json")
    assert compare_files(tmp_path / "no-such-file.json", candidate) == 2
    assert "no-such-file.json" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("invalid json", "invalid JSON"),
        ("[]", "expected a JSON object"),
        ('{"schema_version": 2, "metrics": {}}', "unsupported schema_version"),
        ('{"schema_version": true, "metrics": {}}', "unsupported schema_version"),
        ('{"schema_version": 1}', "missing or invalid metrics object"),
        ('{"schema_version": 1, "metrics": {"p95_ms": "invalid"}}', "invalid metrics"),
        ('{"schema_version": 1, "metrics": {"p95_ms": NaN}}', "invalid metrics"),
    ],
)
def test_rejects_corrupt_or_unsupported_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    data: str,
    expected: str,
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(data, encoding="utf-8")
    candidate = write_result(tmp_path / "candidate.json")

    assert compare_files(baseline, candidate) == 2
    assert expected in capsys.readouterr().err


def test_saved_run_json_is_compatible(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = ExperimentSpec(name="sample", url="http://localhost:8000/health", requests=2)
    baseline_measurement = Measurement(
        latencies_ms=(10.0, 20.0),
        request_count=2,
        successful_requests=2,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    candidate_measurement = Measurement(
        latencies_ms=(20.0, 40.0),
        request_count=2,
        successful_requests=2,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    cli._save_result(baseline, spec, baseline_measurement, calculate_metrics(baseline_measurement))
    cli._save_result(
        candidate, spec, candidate_measurement, calculate_metrics(candidate_measurement)
    )

    assert compare_files(baseline, candidate) == 1
    assert "Overall: REGRESSION" in capsys.readouterr().out


def test_compare_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["compare", "--help"])
    assert exc.value.code == 0
    assert "--p95-latency-pct" in capsys.readouterr().out


def test_exact_threshold_with_rounding_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = write_result(tmp_path / "baseline.json", p95=0.3, error_rate=0.29)
    candidate = write_result(tmp_path / "candidate.json", p95=0.33, error_rate=0.30)

    assert compare_files(baseline, candidate) == 0
    assert "Overall: PASS" in capsys.readouterr().out


def test_clear_regression_takes_precedence_over_missing_p95(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = write_result(tmp_path / "baseline.json", p95=None)
    candidate = write_result(tmp_path / "candidate.json", p95=None, throughput=80)

    assert compare_files(baseline, candidate) == 1
    output = capsys.readouterr().out
    assert "INCONCLUSIVE" in output
    assert "Overall: REGRESSION" in output
