"""Saved-result compatibility and preflight checks for HTTP timeouts."""

import json
from pathlib import Path

import pytest

from regresslab import cli
from regresslab.core import http_runner


def write_result(path: Path, timeout: float | None) -> None:
    # None represents a legacy result written before timeout_seconds existed.
    experiment = {"name": "api", "url": "http://localhost/health", "requests": 2}
    if timeout is not None:
        experiment["timeout_seconds"] = timeout
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment": experiment,
                "measurement": {
                    "latencies_ms": [10.0, 10.0],
                    "request_count": 2,
                    "successful_requests": 2,
                    "failed_requests": 0,
                    "elapsed_seconds": 0.2,
                },
                "metrics": {
                    "p50_ms": 10.0,
                    "p95_ms": 10.0,
                    "p99_ms": 10.0,
                    "successful_throughput_rps": 10.0,
                    "error_rate": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("command", ["compare", "compare-series"])
@pytest.mark.parametrize(
    "before, after, expected", [(None, 5.0, 0), (10.0, 10.0, 0), (None, 10.0, 2)]
)
def test_saved_timeout_compatibility(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    before: float | None,
    after: float,
    expected: int,
) -> None:
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    write_result(baseline, before)
    write_result(candidate, after)
    if command == "compare":
        args = [command, str(baseline), str(candidate)]
    else:
        manifest = tmp_path / "pairs.yaml"
        manifest.write_text(
            "pairs:\n  - id: pair-1\n    baseline: baseline.json\n    candidate: candidate.json\n",
            encoding="utf-8",
        )
        args = [command, str(manifest)]

    assert cli.main(args) == expected
    captured = capsys.readouterr()
    if expected == 2:
        assert "incompatible experiments: timeout_seconds differs" in captured.err
        assert captured.out == ""
    else:
        assert captured.err == ""


def test_rejects_timeout_changes_between_pairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_result(tmp_path / "first.json", 5.0)
    write_result(tmp_path / "second.json", 10.0)
    manifest = tmp_path / "pairs.yaml"
    manifest.write_text(
        "pairs:\n"
        "  - id: first\n    baseline: first.json\n    candidate: first.json\n"
        "  - id: second\n    baseline: second.json\n    candidate: second.json\n",
        encoding="utf-8",
    )
    assert cli.main(["compare-series", str(manifest)]) == 2
    assert "timeout_seconds differs" in capsys.readouterr().err


def test_collection_rejects_different_timeouts_before_http_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_run(*args: object) -> None:
        pytest.fail("Incompatible configurations must not start HTTP requests")

    monkeypatch.setattr(http_runner, "run_experiment", unexpected_run)
    paths = [tmp_path / "baseline.yaml", tmp_path / "candidate.yaml"]
    for path, timeout in zip(paths, (5, 10), strict=True):
        path.write_text(
            f"name: api\nurl: http://localhost/health\nrequests: 2\ntimeout_seconds: {timeout}\n",
            encoding="utf-8",
        )
    output = tmp_path / "series"
    assert (
        cli.main(
            [
                "run-series",
                *(str(path) for path in paths),
                "--repeats",
                "1",
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert "timeout_seconds differs" in capsys.readouterr().err
    assert not output.exists()
