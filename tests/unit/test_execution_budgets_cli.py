"""CLI error codes and incomplete-output protection for execution budgets."""

import asyncio
from pathlib import Path

import httpx
import pytest

from regresslab import cli
from regresslab.core import http_runner


def config(path: Path, port: int, **options: object) -> None:
    content = f"name: api\nurl: http://localhost:{port}/health\nrequests: 1\nconcurrency: 1\n"
    content += "".join(f"{name}: {value}\n" for name, value in options.items())
    path.write_text(content, encoding="utf-8")


def install_client(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]

    def client(**kwargs: object) -> httpx.AsyncClient:
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(http_runner.httpx, "AsyncClient", client)


def test_run_duration_limit_does_not_publish_partial_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def blocked(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        return httpx.Response(200)

    install_client(monkeypatch, blocked)
    source = tmp_path / "run.yaml"
    target = tmp_path / "result.json"
    config(source, 8000, warmup_requests=1, max_duration_seconds=0.02)
    assert cli.main(["run", str(source), "--output", str(target)]) == 1
    assert not target.exists()
    assert "max_duration_seconds" in capsys.readouterr().err


def test_run_rejects_request_cap_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid execution budget must not send requests")

    install_client(monkeypatch, unexpected)
    source = tmp_path / "run.yaml"
    config(source, 8000, warmup_requests=3, max_requests=3)
    assert cli.main(["run", str(source)]) == 1


def test_series_oversized_result_keeps_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, lambda request: httpx.Response(200))
    baseline, candidate = tmp_path / "baseline.yaml", tmp_path / "candidate.yaml"
    for path, port in ((baseline, 8000), (candidate, 8001)):
        config(path, port, max_result_bytes=1)
    output = tmp_path / "series"
    assert (
        cli.main(
            [
                "run-series",
                str(baseline),
                str(candidate),
                "--repeats",
                "1",
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert output.exists()
    assert not list(output.glob("*.json"))
    assert not (output / "pairs.yaml").exists()


def test_series_timeout_preserves_only_completed_legs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8001:
            await asyncio.Event().wait()
        return httpx.Response(200)

    install_client(monkeypatch, handler)
    baseline, candidate = tmp_path / "baseline.yaml", tmp_path / "candidate.yaml"
    for path, port in ((baseline, 8000), (candidate, 8001)):
        config(path, port, max_duration_seconds=0.25)
    output = tmp_path / "series"
    assert (
        cli.main(
            [
                "run-series",
                str(baseline),
                str(candidate),
                "--repeats",
                "1",
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert sorted(path.name for path in output.glob("*.json")) == ["pair-0001-baseline.json"]
    assert not (output / "pairs.yaml").exists()
