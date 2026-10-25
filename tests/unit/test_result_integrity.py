"""Corrupt saved data must never produce a successful statistical decision."""

import json
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import calculate_metrics
from regresslab.core.models import Measurement
from regresslab.core.result_loader import load_saved_result


def _record() -> dict[str, object]:
    spec = ExperimentSpec(name="orders", url="http://localhost:8000/orders", requests=20)
    measurement = Measurement(
        latencies_ms=tuple(float(i) for i in range(1, 21)),
        request_count=20,
        successful_requests=20,
        failed_requests=0,
        elapsed_seconds=0.2,
    )
    return {
        "schema_version": 1,
        "experiment": spec.model_dump(mode="json"),
        "measurement": measurement.model_dump(mode="json"),
        "metrics": calculate_metrics(measurement).model_dump(mode="json"),
    }


def _write(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("measurement", "request_count", 21, "request_count"),
        ("measurement", "successful_requests", 19, "request_count"),
        ("measurement", "failed_requests", 1, "request_count"),
        ("measurement", "latencies_ms", [1.0] * 19, "latencies_ms"),
        ("measurement", "latencies_ms", [1.0] * 19 + [-1.0], "latencies_ms"),
        ("measurement", "latencies_ms", [1.0] * 19 + [float("nan")], "latencies_ms"),
        ("measurement", "request_count", "20", "request_count"),
        ("measurement", "successful_requests", True, "successful_requests"),
        ("measurement", "elapsed_seconds", 0.0, "elapsed_seconds"),
        ("metrics", "p50_ms", 999.0, "metrics.p50_ms"),
        ("metrics", "p95_ms", 999.0, "metrics.p95_ms"),
        ("metrics", "p99_ms", 999.0, "metrics.p99_ms"),
        ("metrics", "successful_throughput_rps", 99.0, "metrics.successful_throughput_rps"),
        ("metrics", "error_rate", 0.05, "metrics.error_rate"),
    ],
)
def test_corruption_is_rejected(
    tmp_path: Path, section: str, field: str, value: object, message: str
) -> None:
    document = _record()
    assert isinstance(document[section], dict)
    document[section][field] = value
    path = _write(tmp_path / "damaged.json", document)
    with pytest.raises(ValueError, match=message):
        load_saved_result(path)


@pytest.mark.parametrize("invalid", [None, {}, [], "bad"])
def test_present_but_invalid_measurement_is_not_legacy(tmp_path: Path, invalid: object) -> None:
    document = _record()
    document["measurement"] = invalid
    with pytest.raises((ValueError, TypeError), match="invalid measurement"):
        load_saved_result(_write(tmp_path / "damaged.json", document))


def test_consistent_counters_must_still_match_experiment(tmp_path: Path) -> None:
    document = _record()
    experiment = document["experiment"]
    assert isinstance(experiment, dict)
    experiment["requests"] = 21
    with pytest.raises(ValueError, match="experiment.requests"):
        load_saved_result(_write(tmp_path / "wrong-workload.json", document))


def test_valid_legacy_and_complete_results_still_load(tmp_path: Path) -> None:
    document = _record()
    complete = load_saved_result(_write(tmp_path / "complete.json", document))
    assert complete.measurement is not None
    document.pop("measurement")
    legacy = load_saved_result(_write(tmp_path / "legacy.json", document))
    assert legacy.measurement is None
    assert legacy.metrics == complete.metrics
    assert (
        cli.main(["compare", str(tmp_path / "legacy.json"), str(tmp_path / "complete.json")]) == 0
    )


def test_tolerance_accepts_serialization_rounding_not_changes(tmp_path: Path) -> None:
    document = _record()
    metrics = document["metrics"]
    assert isinstance(metrics, dict)
    metrics["p95_ms"] = 19.0 + 1e-10
    metrics["successful_throughput_rps"] = 100.0 * (1 + 1e-10)
    assert load_saved_result(_write(tmp_path / "rounded.json", document)).measurement
    metrics["p95_ms"] = 19.001
    with pytest.raises(ValueError, match="metrics.p95_ms"):
        load_saved_result(_write(tmp_path / "changed.json", document))


def test_corrupt_candidate_prevents_pass_and_export(tmp_path: Path) -> None:
    baseline = _write(tmp_path / "before.json", _record())
    damaged = _record()
    metrics = damaged["metrics"]
    assert isinstance(metrics, dict)
    metrics["p95_ms"] = 5.0  # Would misleadingly indicate a performance improvement.
    candidate = _write(tmp_path / "after.json", damaged)
    assert cli.main(["compare", str(baseline), str(candidate)]) == 2
    manifest = tmp_path / "pairs.yaml"
    manifest.write_text(
        "pairs:\n  - id: one\n    baseline: before.json\n    candidate: after.json\n"
    )
    output = tmp_path / "report.json"
    assert cli.main(["compare-series", str(manifest), "--output", str(output)]) == 2
    assert cli.main(["compare-series", str(manifest), "--gate"]) == 2
    assert cli.main(["compare-series", str(manifest), "--gate", "--allow-legacy-metrics"]) == 2
    assert not output.exists()


def test_gate_rejects_legacy_by_default_and_allows_explicit_override(tmp_path: Path) -> None:
    manifest = tmp_path / "pairs.yaml"
    lines = ["pairs:"]
    for index in range(8):
        names = [f"before-{index}.json", f"after-{index}.json"]
        for name in names:
            data = _record()
            data.pop("measurement")
            _write(tmp_path / name, data)
        lines += [
            f"  - id: pair-{index}",
            f"    baseline: {names[0]}",
            f"    candidate: {names[1]}",
        ]
    manifest.write_text("\n".join(lines) + "\n")
    assert cli.main(["compare-series", str(manifest)]) == 0
    assert cli.main(["compare-series", str(manifest), "--gate"]) == 2
    assert cli.main(["compare-series", str(manifest), "--gate", "--allow-legacy-metrics"]) == 0
    assert cli.main(["compare-series", str(manifest), "--allow-legacy-metrics"]) == 2


def test_gate_accepts_valid_complete_samples(tmp_path: Path) -> None:
    manifest = tmp_path / "pairs.yaml"
    lines = ["pairs:"]
    for index in range(8):
        for label in ("baseline", "candidate"):
            _write(tmp_path / f"{index}-{label}.json", _record())
        lines.extend(
            [
                f"  - id: pair-{index}",
                f"    baseline: {index}-baseline.json",
                f"    candidate: {index}-candidate.json",
            ]
        )
    manifest.write_text("\n".join(lines) + "\n")
    assert cli.main(["compare-series", str(manifest), "--gate"]) == 0
