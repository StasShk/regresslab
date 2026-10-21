"""Protect baseline files and publish only complete benchmark JSON."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core.models import Measurement


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "experiment.yaml"
    path.write_text("name: api\nurl: http://localhost/health\nrequests: 1\n")
    return path


def measurement() -> Measurement:
    return Measurement(
        latencies_ms=(10.0,),
        request_count=1,
        successful_requests=1,
        failed_requests=0,
        elapsed_seconds=1.0,
    )


def run(config: Path, output: Path) -> int:
    return cli.main(["run", str(config), "--output", str(output)])


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling", "hardlink"])
def test_existing_output_is_rejected_before_measurement(
    config, tmp_path, monkeypatch, capsys, kind
):
    output = tmp_path / "baseline.json"
    target = tmp_path / "keep.json"
    target.write_text("previous baseline")
    if kind == "file":
        output.write_text("previous baseline")
    elif kind == "directory":
        output.mkdir()
    elif kind == "symlink":
        output.symlink_to(target)
    elif kind == "dangling":
        output.symlink_to(tmp_path / "absent.json")
    else:
        output.hardlink_to(target)
    before = set(tmp_path.iterdir())

    async def unexpected_runner(spec):
        pytest.fail("an existing output must be rejected before sending requests")

    monkeypatch.setattr(cli, "run_experiment", unexpected_runner)
    assert run(config, output) == 1
    assert set(tmp_path.iterdir()) == before
    assert target.read_text() == "previous baseline"
    if kind in {"file", "symlink", "hardlink"}:
        assert output.read_text() == "previous baseline"
    elif kind == "dangling":
        assert output.is_symlink()
        assert not (tmp_path / "absent.json").exists()
    captured = capsys.readouterr()
    assert str(output) in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("kind", ["missing_parent", "parent_is_file"])
def test_invalid_parent_is_rejected_before_measurement(config, tmp_path, monkeypatch, kind):
    parent = tmp_path / "results"
    if kind == "parent_is_file":
        parent.write_text("keep")
    before = set(tmp_path.iterdir())

    async def unexpected_runner(spec):
        pytest.fail("invalid output parent must be rejected before sending requests")

    monkeypatch.setattr(cli, "run_experiment", unexpected_runner)
    assert run(config, parent / "run.json") == 1
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("operation", ["temporary_directory", "hard_link"])
def test_unusable_output_filesystem_is_rejected_before_measurement(
    config,
    tmp_path,
    monkeypatch,
    capsys,
    operation,
):
    def unavailable(*args, **kwargs):
        raise OSError("output filesystem unavailable")

    if operation == "temporary_directory":
        monkeypatch.setattr(tempfile, "mkdtemp", unavailable)
    else:
        monkeypatch.setattr(os, "link", unavailable)

    async def unexpected_runner(spec):
        pytest.fail("an unusable output filesystem must be rejected before requests")

    monkeypatch.setattr(cli, "run_experiment", unexpected_runner)
    assert run(config, tmp_path / "run.json") == 1
    assert "output filesystem unavailable" in capsys.readouterr().err
    assert set(tmp_path.iterdir()) == {config}


def test_file_created_during_benchmark_is_never_overwritten(config, tmp_path, monkeypatch, capsys):
    output = tmp_path / "baseline.json"

    async def runner(spec):
        assert not output.exists()
        output.write_text("another process saved this")
        return measurement()

    monkeypatch.setattr(cli, "run_experiment", runner)
    assert run(config, output) == 1
    assert output.read_text() == "another process saved this"
    assert set(tmp_path.iterdir()) == {config, output}
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("fail_write", [False, True])
def test_final_path_is_not_visible_until_write_completes(
    config,
    tmp_path,
    monkeypatch,
    capsys,
    fail_write,
):
    output = tmp_path / "run.json"
    write_text = Path.write_text

    def observed_write(path, data, *args, **kwargs):
        assert path != output, "write to a temporary file, not the final result"
        assert not output.exists()
        if fail_write:
            write_text(path, data[:10], *args, **kwargs)
            raise OSError("disk full")
        return write_text(path, data, *args, **kwargs)

    async def runner(spec):
        assert not output.exists()
        return measurement()

    monkeypatch.setattr(cli, "run_experiment", runner)
    monkeypatch.setattr(Path, "write_text", observed_write)
    assert run(config, output) == (1 if fail_write else 0)
    captured = capsys.readouterr()
    if fail_write:
        assert "disk full" in captured.err
        assert captured.out == ""
        assert set(tmp_path.iterdir()) == {config}
    else:
        assert json.loads(output.read_text())["measurement"]["request_count"] == 1
        assert captured.err == ""
        assert "Saved JSON:" in captured.out
        assert set(tmp_path.iterdir()) == {config, output}


@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError, KeyboardInterrupt])
def test_failed_or_interrupted_benchmark_leaves_no_output(config, tmp_path, monkeypatch, failure):
    output = tmp_path / "run.json"

    async def runner(spec):
        raise failure("stopped")

    monkeypatch.setattr(cli, "run_experiment", runner)
    if failure is OSError:
        assert run(config, output) == 1
    else:
        with pytest.raises(failure):
            run(config, output)
    assert set(tmp_path.iterdir()) == {config}
