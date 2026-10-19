"""Series collection, durable results, and CLI failure behavior."""

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core import http_runner
from regresslab.core.config import ExperimentSpec, load_experiment
from regresslab.core.models import Measurement
from regresslab.core.series_manifest import load_series_manifest
from regresslab.core.series_runner import run_series


@pytest.fixture
def configs(tmp_path: Path) -> tuple[Path, Path]:
    paths = (tmp_path / "baseline.yaml", tmp_path / "candidate.yaml")
    for path, port in zip(paths, (8000, 8001), strict=True):
        path.write_text(
            f"name: orders\nurl: http://localhost:{port}/orders\nrequests: 2\nconcurrency: 1\n",
            encoding="utf-8",
        )
    return paths


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    ports: list[int] = []
    active = False

    async def measure(spec: ExperimentSpec) -> Measurement:
        nonlocal active
        assert not active, "benchmark runs must not overlap"
        active = True
        ports.append(spec.url.port)
        await asyncio.sleep(0)
        active = False
        # Different values let the test detect reversed baseline/candidate labels.
        latency = 10.0 if spec.url.port == 8000 else 30.0
        return Measurement(
            latencies_ms=(latency, latency),
            request_count=2,
            successful_requests=2,
            failed_requests=0,
            elapsed_seconds=1.0,
        )

    monkeypatch.setattr(http_runner, "run_experiment", measure)
    return ports


def collect(configs: tuple[Path, Path], output: Path, repeats: str = "3") -> int:
    return cli.main(
        [
            "run-series",
            *(str(path) for path in configs),
            "--repeats",
            repeats,
            "--output-dir",
            str(output),
        ]
    )


def test_alternating_runs_produce_portable_manifest(
    configs: tuple[Path, Path],
    tmp_path: Path,
    runs: list[int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "results" / "series"
    assert collect(configs, output) == 0
    assert runs == [8000, 8001, 8001, 8000, 8000, 8001]
    manifest = load_series_manifest(output / "pairs.yaml")
    assert [pair.id for pair in manifest.pairs] == ["pair-0001", "pair-0002", "pair-0003"]
    for pair in manifest.pairs:
        for label, port, latency in (("baseline", 8000, 10.0), ("candidate", 8001, 30.0)):
            relative = getattr(pair, label)
            assert not Path(relative).is_absolute()
            document = json.loads((output / relative).read_text())
            assert document["schema_version"] == 1
            assert document["experiment"]["url"] == f"http://localhost:{port}/orders"
            assert document["measurement"]["request_count"] == 2
            assert document["metrics"]["p95_ms"] == latency
    assert len(list(output.glob("*.json"))) == 6
    assert not list(output.glob("*.tmp"))
    assert "pairs.yaml" in capsys.readouterr().out
    # Moving the whole directory must not break the manifest.
    moved = output.rename(tmp_path / "moved")
    assert cli.main(["compare-series", str(moved / "pairs.yaml")]) == 0
    assert "Pairs: 3" in capsys.readouterr().out


@pytest.mark.parametrize("repeats", ["0", "-1", "1.5"])
def test_invalid_repeats_do_not_start_runs(
    configs: tuple[Path, Path],
    tmp_path: Path,
    runs: list[int],
    repeats: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "series"
    with pytest.raises(SystemExit) as exc:
        collect(configs, output, repeats)
    assert exc.value.code == 2
    assert "must be a positive integer" in capsys.readouterr().err
    assert runs == []
    assert not output.exists()


@pytest.mark.parametrize("failed_file", ["pair-0001-candidate.json.tmp", "pairs.yaml.tmp"])
def test_failed_write_does_not_publish_incomplete_artifacts(
    configs: tuple[Path, Path],
    tmp_path: Path,
    runs: list[int],
    monkeypatch: pytest.MonkeyPatch,
    failed_file: str,
) -> None:
    write_text = Path.write_text

    def interrupted_write(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if path.name == failed_file:
            write_text(path, data[:10], encoding="utf-8")
            raise OSError("disk full")
        return write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", interrupted_write)
    output = tmp_path / "series"
    assert collect(configs, output, "1") == 2
    assert not (output / "pairs.yaml").exists()
    assert (output / failed_file).exists()
    completed = sorted(path.name for path in output.glob("*.json"))
    assert completed == (
        ["pair-0001-baseline.json"]
        if failed_file.startswith("pair-")
        else ["pair-0001-baseline.json", "pair-0001-candidate.json"]
    )
    assert json.loads((output / completed[0]).read_text())["measurement"]["request_count"] == 2


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_existing_output_is_preserved_before_any_requests(
    configs: tuple[Path, Path],
    tmp_path: Path,
    runs: list[int],
    kind: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "series"
    if kind == "directory":
        output.mkdir()
        sentinel = output / "keep.txt"
    elif kind == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        output.symlink_to(target, target_is_directory=True)
        sentinel = target / "keep.txt"
    else:
        sentinel = output
    sentinel.write_text("keep")
    assert collect(configs, output) == 2
    assert runs == []
    assert sentinel.read_text() == "keep"
    assert capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    [
        "name: different\nurl: http://localhost:8001/orders\nrequests: 2\n",
        "name: orders\nurl: http://localhost:8001/other\nrequests: 2\n",
        "name: orders\nurl: http://localhost:8001/orders\nrequests: 3\n",
        "name: orders\nurl: http://localhost:8001/orders\nrequests: 2\nconcurrency: 2\n",
        "broken: [\n",
    ],
)
def test_bad_configs_are_rejected_before_creating_output(
    configs: tuple[Path, Path],
    tmp_path: Path,
    runs: list[int],
    content: str,
) -> None:
    configs[1].write_text(content)
    output = tmp_path / "series"
    assert collect(configs, output) == 2
    assert runs == []
    assert not output.exists()


@pytest.mark.parametrize("failure", [asyncio.CancelledError, OSError])
def test_interrupted_series_keeps_completed_measurements_without_final_manifest(
    configs: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[BaseException],
    capsys: pytest.CaptureFixture[str],
) -> None:
    count = 0

    async def measure(spec: ExperimentSpec) -> Measurement:
        nonlocal count
        count += 1
        if count == 4:
            raise failure("stopped")
        return Measurement(
            latencies_ms=(10.0, 10.0),
            request_count=2,
            successful_requests=2,
            failed_requests=0,
            elapsed_seconds=1.0,
        )

    monkeypatch.setattr(http_runner, "run_experiment", measure)
    output = tmp_path / "series"
    assert collect(configs, output) == (130 if failure is asyncio.CancelledError else 2)
    assert count == 4
    assert not (output / "pairs.yaml").exists()
    assert sorted(path.name for path in output.glob("*.json")) == [
        "pair-0001-baseline.json",
        "pair-0001-candidate.json",
        "pair-0002-candidate.json",
    ]
    for path in output.glob("*.json"):
        assert json.loads(path.read_text())["measurement"]["request_count"] == 2
    captured = capsys.readouterr()
    assert "Saved series manifest" not in captured.out
    assert captured.err


def test_keyboard_interrupt_returns_130(
    configs: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(coroutine: Coroutine[object, object, Path]) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.asyncio, "run", interrupted)
    assert collect(configs, tmp_path / "series") == 130


@pytest.mark.parametrize("repeats", [0, -1, True, 1.5])
def test_python_api_rejects_invalid_repeat_counts(
    configs: tuple[Path, Path],
    tmp_path: Path,
    runs: list[int],
    repeats: object,
) -> None:
    with pytest.raises((ValueError, TypeError), match="repeats"):
        asyncio.run(
            run_series(
                load_experiment(configs[0]),
                load_experiment(configs[1]),
                repeats=repeats,
                output_dir=tmp_path / "series",
            )
        )
    assert not (tmp_path / "series").exists()
    assert runs == []
