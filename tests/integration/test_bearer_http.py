"""Authenticated loopback requests and secret-free saved comparisons."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from test_http_cli import run_cli

from regresslab.core.result_loader import load_saved_result

TOKEN = "integration-private-token.123"


@contextmanager
def protected_server():
    authorized = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            valid = self.headers.get("Authorization") == f"Bearer {TOKEN}"
            authorized.append(valid)
            self.send_response(200 if valid else 401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/health", authorized
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def config(path, url, env):
    path.write_text(
        f"name: api\nurl: {url}\nrequests: 2\nwarmup_requests: 1\nbearer_token_env: {env}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("command", ["run", "run-series"])
def test_authenticated_collection_and_offline_comparison(tmp_path, monkeypatch, command):
    monkeypatch.setenv("BENCH_A", TOKEN)
    monkeypatch.setenv("BENCH_B", TOKEN)
    with protected_server() as (url, authorized):
        for role, env in [("baseline", "BENCH_A"), ("candidate", "BENCH_B")]:
            config(tmp_path / f"{role}.yaml", url, env)
        if command == "run":
            args = ["run", "baseline.yaml", "--output", "result.json"]
        else:
            args = [
                "run-series",
                "baseline.yaml",
                "candidate.yaml",
                "--repeats",
                "2",
                "--output-dir",
                "series",
            ]
        result = run_cli(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    assert authorized == [True] * (3 if command == "run" else 12)
    monkeypatch.delenv("BENCH_A")
    monkeypatch.delenv("BENCH_B")
    for path in tmp_path.rglob("*.json"):
        assert TOKEN not in path.read_text()
        saved = load_saved_result(path)
        assert saved.measurement.successful_requests == 2
        assert saved.measurement.request_count == 2
    if command == "run":
        compared = run_cli(tmp_path, "compare", "result.json", "result.json")
    else:
        compared = run_cli(tmp_path, "compare-series", "series/pairs.yaml")
    assert compared.returncode == 0, compared.stderr
    assert TOKEN not in result.stdout + result.stderr + compared.stdout + compared.stderr


@pytest.mark.parametrize("command, exit_code", [("run", 1), ("run-series", 2)])
def test_missing_token_rejects_whole_collection_before_traffic(
    tmp_path, monkeypatch, command, exit_code
):
    monkeypatch.setenv("BENCH_A", TOKEN)
    monkeypatch.delenv("BENCH_MISSING", raising=False)
    with protected_server() as (url, authorized):
        config(tmp_path / "baseline.yaml", url, "BENCH_A")
        config(tmp_path / "candidate.yaml", url, "BENCH_MISSING")
        args = (
            ["run", "candidate.yaml", "--output", "result.json"]
            if command == "run"
            else [
                "run-series",
                "baseline.yaml",
                "candidate.yaml",
                "--repeats",
                "1",
                "--output-dir",
                "series",
            ]
        )
        result = run_cli(tmp_path, *args)
        assert authorized == []
    assert result.returncode == exit_code
    assert "bearer_token_env" in result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert {p.name for p in tmp_path.iterdir()} == {"baseline.yaml", "candidate.yaml"}


def test_wrong_token_is_a_failed_request_without_secret_in_output(tmp_path, monkeypatch):
    wrong = "incorrect-private-token"
    monkeypatch.setenv("BENCH_A", wrong)
    with protected_server() as (url, authorized):
        config(tmp_path / "experiment.yaml", url, "BENCH_A")
        result = run_cli(tmp_path, "run", "experiment.yaml", "--output", "result.json")
    assert result.returncode == 0, result.stderr
    assert authorized == [False] * 3
    document = (tmp_path / "result.json").read_text()
    assert wrong not in document + result.stdout + result.stderr
    assert json.loads(document)["measurement"]["failed_requests"] == 2
    assert "HTTP 401" in document
