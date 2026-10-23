"""Exercise warmup and execution budgets against real loopback HTTP via the CLI."""

import json
import os
import subprocess
import sysconfig
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import cycle
from pathlib import Path
from threading import Lock, Thread
from time import sleep

import pytest
import yaml


class ServerStats:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.active = 0
        self.peak_active = 0
        self.lock = Lock()


@contextmanager
def http_server(
    *,
    statuses: tuple[int, ...] = (200,),
    delays: tuple[float, ...] = (0.0,),
) -> Iterator[tuple[str, ServerStats]]:
    """Threaded real HTTP server with per-request status/delay and concurrency counters."""
    stats = ServerStats()
    response_codes = cycle(statuses)
    response_delays = cycle(delays)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with stats.lock:
                stats.paths.append(self.path)
                stats.active += 1
                stats.peak_active = max(stats.peak_active, stats.active)
                status = next(response_codes)
                delay = next(response_delays)
            try:
                if delay:
                    sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The duration budget may cancel an in-flight request.
            finally:
                with stats.lock:
                    stats.active -= 1

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/health", stats
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive(), "HTTP server did not stop"


def run_cli(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    executable = Path(sysconfig.get_path("scripts")) / (
        "regresslab.exe" if os.name == "nt" else "regresslab"
    )
    env = os.environ.copy()
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


def config(path: Path, url: str, **overrides: object) -> Path:
    options: dict[str, object] = {
        "name": "execution-budgets-e2e",
        "url": url,
        "requests": 2,
        "concurrency": 1,
        "timeout_seconds": 3.0,
        **overrides,
    }
    path.write_text(yaml.safe_dump(options), encoding="utf-8")
    return path


def test_live_warmup_excludes_requests_failures_and_latency(tmp_path: Path) -> None:
    with http_server(statuses=(503, 204, 200, 500, 201)) as (url, stats):
        input_path = config(
            tmp_path / "run.yaml",
            url,
            requests=3,
            warmup_requests=2,
            max_requests=5,
            max_concurrency=1,
        )
        output = tmp_path / "result.json"
        proc = run_cli(tmp_path, "run", str(input_path), "--output", str(output))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert stats.paths == ["/health"] * 5
    document = json.loads(output.read_text(encoding="utf-8"))
    measured = document["measurement"]
    assert measured["request_count"] == 3
    assert measured["successful_requests"] == 2
    assert measured["failed_requests"] == 1
    assert len(measured["latencies_ms"]) == 2
    assert "HTTP 500" in measured["failure_detail"]
    assert document["metrics"]["error_rate"] == pytest.approx(1 / 3)
    assert document["metrics"]["successful_throughput_rps"] == pytest.approx(
        2 / measured["elapsed_seconds"]
    )


def test_live_warmup_delay_does_not_inflate_measured_elapsed(tmp_path: Path) -> None:
    with http_server(delays=(0.45, 0, 0)) as (url, stats):
        input_path = config(tmp_path / "run.yaml", url, warmup_requests=1)
        output = tmp_path / "result.json"
        proc = run_cli(tmp_path, "run", str(input_path), "--output", str(output))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(stats.paths) == 3
    measured = json.loads(output.read_text(encoding="utf-8"))["measurement"]
    assert measured["elapsed_seconds"] < 0.35  # Delayed warmup is excluded.


def test_live_http_concurrency_respects_cap_in_both_phases(tmp_path: Path) -> None:
    with http_server(delays=(0.12,)) as (url, stats):
        input_path = config(
            tmp_path / "run.yaml",
            url,
            requests=9,
            warmup_requests=6,
            concurrency=3,
            max_concurrency=3,
            max_requests=15,
        )
        output = tmp_path / "result.json"
        proc = run_cli(tmp_path, "run", str(input_path), "--output", str(output))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(stats.paths) == 15
    assert stats.peak_active == 3
    measured = json.loads(output.read_text(encoding="utf-8"))["measurement"]
    assert measured["request_count"] == measured["successful_requests"] == 9


@pytest.mark.parametrize("warmup_requests", [0, 1])
def test_live_deadline_cancels_http_without_publishing_partial_result(
    tmp_path: Path, warmup_requests: int
) -> None:
    # Give the HTTP client enough time to start, then block the live response
    # longer than the total budget. A 0.15s budget can expire before any GET.
    with http_server(delays=(12.0,)) as (url, stats):
        input_path = config(
            tmp_path / "run.yaml",
            url,
            warmup_requests=warmup_requests,
            max_duration_seconds=3.0,
            timeout_seconds=20.0,  # Must not let HTTPX's per-I/O timeout win.
        )
        output = tmp_path / "result.json"
        proc = run_cli(tmp_path, "run", str(input_path), "--output", str(output))
    assert proc.returncode == 1
    assert "max_duration_seconds" in proc.stderr
    assert proc.stdout == ""
    assert stats.paths  # An HTTP request really started before cancellation.
    assert not output.exists()
    assert not list(tmp_path.glob(".regresslab-*"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"requests": 3, "warmup_requests": 2, "max_requests": 4},
        {"concurrency": 3, "max_concurrency": 2},
    ],
)
def test_live_preflight_budget_rejects_before_network(
    tmp_path: Path, overrides: dict[str, int]
) -> None:
    with http_server() as (url, stats):
        input_path = config(tmp_path / "run.yaml", url, **overrides)
        output = tmp_path / "result.json"
        proc = run_cli(tmp_path, "run", str(input_path), "--output", str(output))
    assert proc.returncode == 1
    assert "regresslab:" in proc.stderr
    assert proc.stdout == ""
    assert stats.paths == []
    assert not output.exists()


def test_live_result_size_cap_does_not_publish_json(tmp_path: Path) -> None:
    with http_server() as (url, stats):
        input_path = config(tmp_path / "run.yaml", url, max_result_bytes=16)
        output = tmp_path / "result.json"
        proc = run_cli(tmp_path, "run", str(input_path), "--output", str(output))
    assert proc.returncode == 1
    assert "max_result_bytes" in proc.stderr
    assert stats.paths == ["/health"] * 2
    assert not output.exists()
    assert not list(tmp_path.glob(".regresslab-*"))


def test_live_series_warmup_does_not_change_saved_measurements(tmp_path: Path) -> None:
    output = tmp_path / "series"
    with (
        http_server() as (baseline_url, baseline),
        http_server(statuses=(500, 200)) as (candidate_url, candidate),
    ):
        for label, url in (("baseline", baseline_url), ("candidate", candidate_url)):
            config(
                tmp_path / f"{label}.yaml",
                url,
                warmup_requests=1,
                max_requests=3,
                max_duration_seconds=5.0,
                max_result_bytes=10000,
            )
        proc = run_cli(
            tmp_path,
            "run-series",
            "baseline.yaml",
            "candidate.yaml",
            "--repeats",
            "2",
            "--output-dir",
            str(output),
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(baseline.paths) == len(candidate.paths) == 6
    manifest = yaml.safe_load((output / "pairs.yaml").read_text(encoding="utf-8"))
    assert len(manifest["pairs"]) == 2
    for pair in manifest["pairs"]:
        for role, failures in (("baseline", 0), ("candidate", 1)):
            document = json.loads((output / pair[role]).read_text(encoding="utf-8"))
            assert document["experiment"]["warmup_requests"] == 1
            assert document["measurement"]["request_count"] == 2
            assert document["measurement"]["failed_requests"] == failures
            assert document["metrics"]["error_rate"] == failures / 2
    assert not list(output.glob("*.tmp"))


def test_live_series_deadline_leaves_completed_leg_but_no_manifest(tmp_path: Path) -> None:
    output = tmp_path / "series"
    with (
        http_server() as (baseline_url, baseline),
        http_server(delays=(12.0,)) as (candidate_url, candidate),
    ):
        for label, url in (("baseline", baseline_url), ("candidate", candidate_url)):
            config(
                tmp_path / f"{label}.yaml",
                url,
                requests=1,
                warmup_requests=1,
                max_duration_seconds=4.0,
                timeout_seconds=20.0,
            )
        proc = run_cli(
            tmp_path,
            "run-series",
            "baseline.yaml",
            "candidate.yaml",
            "--repeats",
            "1",
            "--output-dir",
            str(output),
        )
    assert proc.returncode == 2
    assert "max_duration_seconds" in proc.stderr
    assert len(baseline.paths) == 2
    assert candidate.paths  # The second leg started before the deadline.
    assert (output / "pair-0001-baseline.json").exists()
    assert not (output / "pair-0001-candidate.json").exists()
    assert not (output / "pairs.yaml").exists()
