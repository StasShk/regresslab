"""Exercise the installed CLI through real loopback HTTP and saved JSON files."""

import json
import os
import subprocess
import sysconfig
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import cycle
from pathlib import Path
from threading import Thread

import yaml


@contextmanager
def http_server(statuses: tuple[int, ...]) -> Iterator[tuple[str, list[str]]]:
    responses = cycle(statuses)
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            self.send_response(next(responses))
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/health", paths
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive(), "HTTP server did not stop"


def run_cli(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    executable = Path(sysconfig.get_path("scripts")) / (
        "regresslab.exe" if os.name == "nt" else "regresslab"
    )
    env = os.environ.copy()
    # Loopback traffic must stay local even on machines with a configured proxy.
    for key in list(env):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "pythonpath"}:
            del env[key]
    env["NO_PROXY"] = "127.0.0.1,localhost"
    return subprocess.run(
        [str(executable), *args],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_http_run_save_and_compare(tmp_path: Path) -> None:
    for label, statuses, failures in (
        ("baseline", (200,), 0),
        ("candidate", (200, 503), 4),
    ):
        config = tmp_path / f"{label}.yaml"
        output = tmp_path / f"{label}.json"
        with http_server(statuses) as (url, paths):
            config.write_text(
                f"name: http-e2e\nurl: {url}\nrequests: 8\nconcurrency: 2\n",
                encoding="utf-8",
            )
            result = run_cli(tmp_path, "run", str(config), "--output", str(output))

        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stderr == ""
        assert paths == ["/health"] * 8
        saved = json.loads(output.read_text(encoding="utf-8"))
        assert saved["schema_version"] == 1
        assert saved["experiment"]["url"] == url
        measurement = saved["measurement"]
        assert measurement["request_count"] == 8
        assert measurement["successful_requests"] == 8 - failures
        assert measurement["failed_requests"] == failures
        assert len(measurement["latencies_ms"]) == 8 - failures
        assert measurement["elapsed_seconds"] > 0
        metrics = saved["metrics"]
        assert metrics["error_rate"] == failures / 8
        assert metrics["p95_ms"] > 0
        assert metrics["successful_throughput_rps"] > 0

    unchanged = run_cli(tmp_path, "compare", "baseline.json", "baseline.json")
    assert unchanged.returncode == 0, unchanged.stdout + unchanged.stderr
    assert unchanged.stderr == ""
    assert "Overall: PASS" in unchanged.stdout

    regressed = run_cli(
        tmp_path,
        "compare",
        "baseline.json",
        "candidate.json",
        # Detect errors without relying on latency differences or scheduler timing.
        "--p95-latency-pct",
        "1000000000",
        "--throughput-pct",
        "100",
        "--error-rate-points",
        "1",
    )
    assert regressed.returncode == 1, regressed.stdout + regressed.stderr
    assert regressed.stderr == ""
    assert "Overall: REGRESSION" in regressed.stdout
    error_row = next(
        line for line in regressed.stdout.splitlines() if line.startswith("Error rate")
    )
    assert error_row.split() == [
        "Error",
        "rate",
        "0.00%",
        "50.00%",
        "+50.00",
        "pp",
        "1.00",
        "pp",
        "REGRESSION",
    ]

    # Servers are stopped: series comparison must consume saved files only.
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    manifest = manifests / "pairs.yaml"
    manifest.write_text(
        "pairs:\n"
        "  - id: unchanged\n"
        "    baseline: ../baseline.json\n"
        "    candidate: ../baseline.json\n"
        "  - id: changed\n"
        "    baseline: ../baseline.json\n"
        "    candidate: ../candidate.json\n",
        encoding="utf-8",
    )
    comparison_path = tmp_path / "comparison.json"
    series_report = run_cli(
        tmp_path,
        "compare-series",
        str(manifest),
        "--output",
        str(comparison_path),
        "--p95-latency-pct",
        "1000000000",
        "--throughput-pct",
        "100",
    )
    assert series_report.returncode == 0, series_report.stdout + series_report.stderr
    assert series_report.stderr == ""
    assert "no series-wide verdict" in series_report.stdout
    document = json.loads(comparison_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    series = document["series"]
    assert [pair["pair_id"] for pair in series["pairs"]] == ["unchanged", "changed"]
    assert series["error_rate"]["pass_count"] == 1
    assert series["error_rate"]["regression_count"] == 1
    assert series["error_rate"]["median_change"] == 25.0
    assert series["pairs"][1]["comparison"]["error_rate"]["candidate"] == 0.5
    assert "status" not in series


def test_http_collect_series_and_compare_offline(tmp_path: Path) -> None:
    output = tmp_path / "results"
    with (
        http_server((200,)) as (baseline_url, baseline_paths),
        http_server((200, 503)) as (candidate_url, candidate_paths),
    ):
        for label, url in (("baseline", baseline_url), ("candidate", candidate_url)):
            (tmp_path / f"{label}.yaml").write_text(
                f"name: series-e2e\nurl: {url}\nrequests: 4\nconcurrency: 1\n",
                encoding="utf-8",
            )
        collected = run_cli(
            tmp_path,
            "run-series",
            "baseline.yaml",
            "candidate.yaml",
            "--repeats",
            "2",
            "--output-dir",
            str(output),
        )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert collected.stderr == ""
    assert len(baseline_paths) == len(candidate_paths) == 8
    manifest = yaml.safe_load((output / "pairs.yaml").read_text(encoding="utf-8"))
    assert len(manifest["pairs"]) == 2
    for pair in manifest["pairs"]:
        for label, failures in (("baseline", 0), ("candidate", 2)):
            saved = json.loads((output / pair[label]).read_text(encoding="utf-8"))
            assert saved["measurement"]["failed_requests"] == failures
            assert saved["measurement"]["request_count"] == 4
            assert saved["metrics"]["error_rate"] == failures / 4
    # Both servers are stopped; comparisons need only the generated files.
    compared = run_cli(
        tmp_path,
        "compare-series",
        str(output / "pairs.yaml"),
        "--output",
        "comparison.json",
        "--p95-latency-pct",
        "1000000000",
        "--throughput-pct",
        "100",
    )
    assert compared.returncode == 0, compared.stdout + compared.stderr
    assert compared.stderr == ""
    report = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert report["series"]["error_rate"]["regression_count"] == 2
    assert report["series"]["error_rate"]["median_change"] == 50.0


def test_invalid_output_does_not_send_http_requests(tmp_path: Path) -> None:
    config = tmp_path / "experiment.yaml"
    baseline = tmp_path / "baseline.json"
    baseline.write_text("preserve this baseline", encoding="utf-8")
    with http_server((200,)) as (url, paths):
        config.write_text(f"name: output-check\nurl: {url}\nrequests: 4\n", encoding="utf-8")
        for output in (baseline, tmp_path / "missing" / "run.json"):
            result = run_cli(tmp_path, "run", str(config), "--output", str(output))
            assert result.returncode == 1, result.stdout + result.stderr
            assert result.stdout == ""
            assert "regresslab:" in result.stderr
        assert paths == []
    assert baseline.read_text(encoding="utf-8") == "preserve this baseline"
    assert set(tmp_path.iterdir()) == {config, baseline}
