"""Opt-in statistical series gate integration and exit-code contract."""

import json
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import BenchmarkMetrics


def gate_cli(argv: list[str]) -> int:
    """Older fixture files are metrics-only; explicitly permit them for gate tests."""
    return cli.main([*argv, "--allow-legacy-metrics"] if "--gate" in argv else argv)


def save_result(
    path: Path,
    *,
    p95: float | None = 100.0,
    throughput: float | None = 100.0,
    error_rate: float | None = 0.0,
) -> None:
    experiment = ExperimentSpec(
        name="api", url="http://localhost:8000/health", requests=100, concurrency=5
    )
    metrics = BenchmarkMetrics(
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
                "experiment": experiment.model_dump(mode="json"),
                "metrics": metrics.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )


def write_manifest(path: Path, pairs: list[tuple[str, str, str]]) -> Path:
    lines = ["pairs:"]
    for identifier, baseline, candidate in pairs:
        lines += [
            f"  - id: {identifier}",
            f"    baseline: {baseline}",
            f"    candidate: {candidate}",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _series(
    tmp_path: Path,
    *,
    count: int = 8,
    p95: float | None = 100,
    throughput: float | None = 100,
    error_rate: float | None = 0,
) -> Path:
    paths = []
    for i in range(count):
        baseline = f"baseline-{i}.json"
        candidate = f"candidate-{i}.json"
        save_result(tmp_path / baseline)
        save_result(tmp_path / candidate, p95=p95, throughput=throughput, error_rate=error_rate)
        paths.append((f"pair-{i}", baseline, candidate))
    return write_manifest(tmp_path / "pairs.yaml", paths)


def test_gate_pass_with_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _series(tmp_path)
    output = tmp_path / "gate.json"
    assert gate_cli(["compare-series", str(manifest), "--gate", "--output", str(output)]) == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["series"]["pairs"][0]["pair_id"] == "pair-0"
    assert document["gate"]["status"] == "PASS"
    assert document["gate"]["p95_latency_ms"]["upper_bound"] == 0
    assert "Statistical gate: PASS" in capsys.readouterr().out


@pytest.mark.parametrize("changes", [{"p95": 125}, {"throughput": 70}, {"error_rate": 0.04}])
def test_gate_regression_returns_one(tmp_path: Path, changes: dict[str, float]) -> None:
    manifest = _series(tmp_path, **changes)
    assert gate_cli(["compare-series", str(manifest), "--gate"]) == 1


def test_gate_insufficient_pairs_never_passes(tmp_path: Path) -> None:
    manifest = _series(tmp_path, count=7)
    assert gate_cli(["compare-series", str(manifest), "--gate"]) == 3


def test_gate_undefined_throughput_is_inconclusive(tmp_path: Path) -> None:
    paths = []
    for i in range(8):
        before, after = f"before-{i}.json", f"after-{i}.json"
        save_result(tmp_path / before, throughput=0)
        save_result(tmp_path / after, throughput=0)
        paths.append((f"pair-{i}", before, after))
    manifest = write_manifest(tmp_path / "pairs.yaml", paths)
    assert gate_cli(["compare-series", str(manifest), "--gate"]) == 3


def test_gate_missing_p95_is_inconclusive(tmp_path: Path) -> None:
    manifest = _series(tmp_path, p95=None)
    assert gate_cli(["compare-series", str(manifest), "--gate"]) == 3


def test_gate_rejects_reusing_baseline_across_pairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_result(tmp_path / "baseline.json")
    paths = []
    for i in range(8):
        name = f"candidate-{i}.json"
        save_result(tmp_path / name)
        paths.append((f"pair-{i}", "baseline.json", name))
    manifest = write_manifest(tmp_path / "pairs.yaml", paths)
    output = tmp_path / "report.json"
    assert gate_cli(["compare-series", str(manifest), "--gate", "--output", str(output)]) == 2
    assert "reuses pair 'pair-0' baseline" in capsys.readouterr().err
    assert not output.exists()
    # A descriptive report has never required independently sourced runs.
    assert cli.main(["compare-series", str(manifest)]) == 0


def test_gate_rejects_same_result_on_both_sides(tmp_path: Path) -> None:
    save_result(tmp_path / "result.json")
    manifest = write_manifest(tmp_path / "pairs.yaml", [("same", "result.json", "result.json")])
    assert gate_cli(["compare-series", str(manifest), "--gate"]) == 2


def test_gate_rejects_source_path_alias(tmp_path: Path) -> None:
    save_result(tmp_path / "original.json")
    (tmp_path / "alias.json").symlink_to(tmp_path / "original.json")
    save_result(tmp_path / "other.json")
    manifest = write_manifest(
        tmp_path / "pairs.yaml",
        [("one", "original.json", "other.json"), ("two", "alias.json", "other.json")],
    )
    assert gate_cli(["compare-series", str(manifest), "--gate"]) == 2


def test_descriptive_mode_preserves_exit_code_and_json(tmp_path: Path) -> None:
    manifest = _series(tmp_path, p95=125)
    output = tmp_path / "report.json"
    assert gate_cli(["compare-series", str(manifest), "--output", str(output)]) == 0
    assert "gate" not in json.loads(output.read_text(encoding="utf-8"))


def test_gate_accepts_configuration(tmp_path: Path) -> None:
    manifest = _series(tmp_path, count=12)
    output = tmp_path / "report.json"
    argv = [
        "compare-series",
        str(manifest),
        "--gate",
        "--min-pairs",
        "12",
        "--confidence",
        "0.95",
        "--p95-latency-pct",
        "10",
        "--throughput-pct",
        "10",
        "--error-rate-points",
        "1",
        "--output",
        str(output),
    ]
    assert gate_cli(argv) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["gate"]["min_pairs"] == 12


@pytest.mark.parametrize("confidence", ["0", "1", "nan", "inf"])
def test_invalid_confidence_is_error(tmp_path: Path, confidence: str) -> None:
    manifest = _series(tmp_path)
    assert gate_cli(["compare-series", str(manifest), "--gate", "--confidence", confidence]) == 2


def test_gate_settings_without_gate_are_error(tmp_path: Path) -> None:
    manifest = _series(tmp_path)
    assert gate_cli(["compare-series", str(manifest), "--confidence", "0.95"]) == 2


def test_gate_output_never_overwrites_input(tmp_path: Path) -> None:
    manifest = _series(tmp_path)
    original = manifest.read_bytes()
    assert gate_cli(["compare-series", str(manifest), "--gate", "--output", str(manifest)]) == 2
    assert manifest.read_bytes() == original
