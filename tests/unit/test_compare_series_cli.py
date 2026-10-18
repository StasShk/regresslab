"""CLI tests for series comparison of saved benchmark JSON files."""

import json
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics


def save_result(
    path: Path,
    *,
    p95: float | None = 100.0,
    throughput: float | None = 100.0,
    error_rate: float | None = 0.0,
    spec: ExperimentSpec | None = None,
) -> None:
    if spec is None:
        spec = ExperimentSpec(
            name="api", url="http://localhost:8000/health", requests=100, concurrency=5
        )
    values = BenchmarkMetrics(
        p50_ms=p95,
        p95_ms=p95,
        p99_ms=p95,
        successful_throughput_rps=throughput,
        error_rate=error_rate,
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment": spec.model_dump(mode="json"),
                "metrics": values.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )


def write_manifest(path: Path, pairs: list[tuple[str, str, str]]) -> Path:
    lines = ["pairs:"]
    for identifier, baseline, candidate in pairs:
        lines.extend(
            [
                f"  - id: {identifier}",
                f"    baseline: {baseline}",
                f"    candidate: {candidate}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_series(path: Path, *options: str) -> int:
    return cli.main(["compare-series", str(path), *options])


def test_series_from_saved_results_resolves_relative_paths_and_preserves_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = tmp_path / "benchmark-results"
    directory.mkdir()
    save_result(directory / "baseline.json")
    save_result(directory / "candidate.json", p95=130, throughput=75, error_rate=0.03)
    manifest = write_manifest(
        directory / "pairs.yaml",
        [
            ("stable", "baseline.json", "baseline.json"),
            ("changed", "baseline.json", "candidate.json"),
        ],
    )
    monkeypatch.chdir(tmp_path)
    assert run_series(manifest) == 0
    report = capsys.readouterr().out
    assert report.index("stable") < report.index("changed")
    assert "Pairs: 2" in report
    assert "+30.00% (REGRESSION)" in report
    assert "-25.00% (REGRESSION)" in report
    assert "+3.00 pp (REGRESSION)" in report
    assert "+15.00%" in report  # Median of paired p95 changes (0 and +30).
    assert "-12.50%" in report
    assert "+1.50 pp" in report
    assert "no series-wide verdict" in report
    assert "Series status:" not in report


def test_threshold_overrides_apply_to_each_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_result(tmp_path / "before.json")
    save_result(tmp_path / "after.json", p95=115)
    manifest = write_manifest(tmp_path / "pairs.yaml", [("run-1", "before.json", "after.json")])
    assert run_series(manifest, "--p95-latency-pct", "20") == 0
    report = capsys.readouterr().out
    row = next(line for line in report.splitlines() if line.startswith("run-1"))
    assert "+15.00% (PASS)" in row
    assert row.endswith("PASS")
    assert "p95 +20%" in report


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "invalid series manifest"),
        ("pairs: []\n", "at least 1 item"),
        ("pairs: invalid\n", "invalid series manifest"),
        ("pairs: [\n", "expected the node content"),
        (
            (
                "pairs:\n"
                "  - id: repeated\n    baseline: a.json\n    candidate: b.json\n"
                "  - id: repeated\n    baseline: a.json\n    candidate: b.json\n"
            ),
            "pair IDs must be unique",
        ),
        (
            "pairs:\n  - id: '  '\n    baseline: a.json\n    candidate: b.json\n",
            "invalid series manifest",
        ),
        (
            "pairs:\n  - id: a\n    baseline: a.json\n    candidate: b.json\n    extra: x\n",
            "Extra inputs are not permitted",
        ),
    ],
)
def test_bad_manifest_returns_input_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    text: str,
    expected: str,
) -> None:
    manifest = tmp_path / "pairs.yaml"
    manifest.write_text(text, encoding="utf-8")
    assert run_series(manifest) == 2
    assert expected in capsys.readouterr().err


def test_missing_result_and_invalid_threshold_return_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_result(tmp_path / "baseline.json")
    manifest = write_manifest(tmp_path / "pairs.yaml", [("first", "baseline.json", "missing.json")])
    assert run_series(manifest) == 2
    assert "missing.json" in capsys.readouterr().err
    assert run_series(manifest, "--error-rate-points", "nan") == 2
    assert "finite" in capsys.readouterr().err.lower()


def test_incompatible_pair_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    save_result(tmp_path / "baseline.json")
    altered = ExperimentSpec(name="api", url="http://localhost/other", requests=100)
    save_result(tmp_path / "candidate.json", spec=altered)
    manifest = write_manifest(tmp_path / "pairs.yaml", [("bad", "baseline.json", "candidate.json")])
    assert run_series(manifest) == 2
    err = capsys.readouterr().err
    assert "pair 'bad'" in err
    assert "URL path differs" in err


def test_incompatible_workloads_between_pairs_are_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_result(tmp_path / "first.json")
    changed = ExperimentSpec(name="api", url="http://localhost/health", requests=200, concurrency=5)
    save_result(tmp_path / "second.json", spec=changed)
    manifest = write_manifest(
        tmp_path / "pairs.yaml",
        [("one", "first.json", "first.json"), ("two", "second.json", "second.json")],
    )
    assert run_series(manifest) == 2
    err = capsys.readouterr().err
    assert "pair 'two'" in err
    assert "requests differs" in err


def test_different_hosts_are_allowed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    save_result(tmp_path / "first.json")
    remote = ExperimentSpec(
        name="api", url="http://candidate.example:9999/health", requests=100, concurrency=5
    )
    save_result(tmp_path / "second.json", spec=remote)
    manifest = write_manifest(
        tmp_path / "pairs.yaml", [("host-change", "first.json", "second.json")]
    )
    assert run_series(manifest) == 0
    assert "host-change" in capsys.readouterr().out


def test_inconclusive_metrics_are_shown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    save_result(tmp_path / "baseline.json", p95=None, throughput=0, error_rate=1)
    save_result(tmp_path / "candidate.json", p95=None, throughput=0, error_rate=1)
    manifest = write_manifest(
        tmp_path / "pairs.yaml", [("none", "baseline.json", "candidate.json")]
    )
    assert run_series(manifest) == 0
    output = capsys.readouterr().out
    assert "INCONCLUSIVE" in output
    assert "n/a" in output
    assert "no series-wide verdict" in output


def test_compare_series_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["compare-series", "--help"])
    assert exc.value.code == 0
    assert "0=REPORT GENERATED, 2=ERROR" in capsys.readouterr().out


def test_json_output_preserves_pairs_thresholds_and_missing_values(tmp_path: Path) -> None:
    save_result(tmp_path / "before.json")
    save_result(tmp_path / "after.json", p95=None, throughput=70)
    manifest = write_manifest(
        tmp_path / "pairs.yaml",
        [("stable", "before.json", "before.json"), ("changed", "before.json", "after.json")],
    )
    output = tmp_path / "comparison.json"
    assert run_series(manifest, "--output", str(output), "--throughput-pct", "20") == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    series = document["series"]
    assert series["thresholds"]["throughput_pct"] == 20
    assert [pair["pair_id"] for pair in series["pairs"]] == ["stable", "changed"]
    changed = series["pairs"][1]["comparison"]
    assert changed["p95_latency_ms"]["candidate"] is None
    assert changed["p95_latency_ms"]["reason"]
    assert changed["successful_throughput_rps"]["change_pct"] == -30
    assert series["p95_latency_ms"]["inconclusive_count"] == 1
    assert series["successful_throughput_rps"]["median_change"] == -15
    assert "status" not in series


@pytest.mark.parametrize("filename", ["pairs.yaml", "before.json", "existing.json"])
def test_output_never_overwrites_an_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str
) -> None:
    save_result(tmp_path / "before.json")
    manifest = write_manifest(tmp_path / "pairs.yaml", [("one", "before.json", "before.json")])
    (tmp_path / "existing.json").write_text("keep this", encoding="utf-8")
    output = tmp_path / filename
    original = output.read_bytes()
    assert run_series(manifest, "--output", str(output)) == 2
    assert output.read_bytes() == original
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "regresslab:" in captured.err


def test_output_write_failure_returns_error_without_success_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_result(tmp_path / "before.json")
    manifest = write_manifest(tmp_path / "pairs.yaml", [("one", "before.json", "before.json")])
    assert run_series(manifest, "--output", str(tmp_path / "missing" / "out.json")) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "regresslab:" in captured.err


def test_invalid_input_does_not_create_output(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path / "pairs.yaml", [("one", "absent.json", "absent.json")])
    output = tmp_path / "out.json"
    assert run_series(manifest, "--output", str(output)) == 2
    assert not output.exists()
