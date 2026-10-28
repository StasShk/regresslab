"""Input validation and atomic comparison output regression tests."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import calculate_metrics
from regresslab.core.models import Measurement
from regresslab.core.saved_results import save_result


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nested_nonfinite_json_rejected(value: float) -> None:
    with pytest.raises(ValidationError, match="json_body must contain only finite JSON values"):
        ExperimentSpec(
            name="invalid",
            url="http://localhost/test",
            requests=1,
            method="POST",
            json_body={"nested": [1, {"value": value}]},
        )


@pytest.mark.parametrize("body", [".nan", ".inf", "-.inf", "!!set {x: null}"])
@pytest.mark.parametrize("command", ["run", "run-series"])
def test_invalid_body_never_starts_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, command: str
) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text(
        "name: invalid\nurl: http://localhost/test\nrequests: 1\nmethod: POST\n"
        f"json_body:\n  nested:\n    value: {body}\n",
        encoding="utf-8",
    )

    async def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("An invalid workload started HTTP requests")

    monkeypatch.setattr(cli, "run_experiment", unexpected_run)
    monkeypatch.setattr(cli, "run_series", unexpected_run)
    output = tmp_path / ("result.json" if command == "run" else "runs")
    args = (
        ["run", str(config), "--output", str(output)]
        if command == "run"
        else [
            "run-series",
            str(config),
            str(config),
            "--repeats",
            "1",
            "--output-dir",
            str(output),
        ]
    )
    assert cli.main(args) == (1 if command == "run" else 2)
    assert not output.exists()


def _manifest(tmp_path: Path) -> Path:
    spec = ExperimentSpec(name="api", url="http://localhost/test", requests=1)
    measurement = Measurement(
        latencies_ms=(10.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
    )
    for name in ("baseline.json", "candidate.json"):
        save_result(tmp_path / name, spec, measurement, calculate_metrics(measurement))
    manifest = tmp_path / "pairs.yaml"
    manifest.write_text(
        "pairs:\n  - id: one\n    baseline: baseline.json\n    candidate: candidate.json\n",
        encoding="utf-8",
    )
    return manifest


def test_failed_write_does_not_publish_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path)
    output = tmp_path / "comparison.json"
    original_write = Path.write_text

    def broken_write(path: Path, content: str, *args: object, **kwargs: object) -> int:
        if path.name == "result.json" and path.parent.name.startswith(".regresslab-"):
            original_write(path, content[:15], *args, **kwargs)
            raise OSError("simulated write failure")
        return original_write(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", broken_write)
    assert cli.main(["compare-series", str(manifest), "--output", str(output)]) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".regresslab-*"))
    assert capsys.readouterr().out == ""


def test_competing_writer_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path)
    output = tmp_path / "comparison.json"
    original_link = os.link

    def competing_link(source: object, target: object, *args: object, **kwargs: object) -> None:
        if Path(target) == output:
            output.write_text("another process", encoding="utf-8")
        original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", competing_link)
    assert cli.main(["compare-series", str(manifest), "--output", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "another process"
    assert not list(tmp_path.glob(".regresslab-*"))
    assert capsys.readouterr().out == ""
