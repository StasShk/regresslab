"""Request errors and CLI output must not expose target addresses or secrets."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from regresslab import cli
from regresslab.core.config import ExperimentSpec
from regresslab.core.http_runner import measure_http_request
from regresslab.core.metrics import calculate_metrics
from regresslab.core.models import Measurement
from regresslab.core.output_sanitization import safe_failure_summary
from regresslab.core.saved_results import save_result


@pytest.mark.parametrize("kind", [httpx.ConnectError, httpx.ReadTimeout])
def test_httpx_message_does_not_enter_failure_detail(kind: type[httpx.RequestError]) -> None:
    url = "http://user:password@private.example:8000/sensitive?token=secret"

    def handler(request: httpx.Request) -> httpx.Response:
        raise kind(f"connect failed at {url}: auth=secret", request=request)

    async def execute() -> str | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await measure_http_request(url, client=client)
        assert not result.succeeded
        return result.failure_detail

    assert asyncio.run(execute()) == kind.__name__


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("2 requests failed; first error: HTTP 503", "2 requests failed; first error: HTTP 503"),
        (
            "2 requests failed; first error: ConnectTimeout",
            "2 requests failed; first error: ConnectTimeout",
        ),
        (
            "2 requests failed; first error: HTTP 503\nBearer secret",
            "2 requests failed; first error: redacted",
        ),
        (
            "1 requests failed; first error: HTTP 503",
            "2 requests failed; first error: redacted",
        ),
        (
            "2 requests failed; first error: private.internal",
            "2 requests failed; first error: redacted",
        ),
        (None, "2 requests failed; first error: redacted"),
    ],
)
def test_only_allowlisted_failure_reasons_survive(detail: str | None, expected: str) -> None:
    assert safe_failure_summary(2, detail) == expected
    assert safe_failure_summary(0, detail) is None


def test_run_cli_does_not_echo_target_or_injected_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "private-token-123"
    config = tmp_path / "run.yaml"
    config.write_text(
        "name: api\n"
        f"url: http://user:password@secret.internal:8000/path/{secret}?auth={secret}\n"
        "requests: 1\n",
        encoding="utf-8",
    )

    async def runner(_: ExperimentSpec) -> Measurement:
        return Measurement(
            latencies_ms=(),
            request_count=1,
            successful_requests=0,
            failed_requests=1,
            elapsed_seconds=0.1,
            failure_detail=f"1 requests failed; first error: ConnectError: {secret}",
        )

    monkeypatch.setattr(cli, "run_experiment", runner)
    assert cli.main(["run", str(config)]) == 0
    captured = capsys.readouterr()
    assert "Target: http://[redacted]" in captured.out
    assert "Failures: 1 requests failed; first error: redacted" in captured.out
    for forbidden in (secret, "secret.internal", "user:password"):
        assert forbidden not in captured.out + captured.err


def test_direct_save_sanitizes_untrusted_measurement_but_preserves_status(tmp_path: Path) -> None:
    spec = ExperimentSpec(name="api", url="http://localhost:8000/health", requests=1)
    secret = "credentials-auth-token"
    measurement = Measurement(
        latencies_ms=(),
        request_count=1,
        successful_requests=0,
        failed_requests=1,
        elapsed_seconds=0.5,
        failure_detail=f"1 requests failed; first error: ReadTimeout: {secret}",
    )
    path = tmp_path / "saved.json"
    save_result(path, spec, measurement, calculate_metrics(measurement))
    serialized = path.read_text(encoding="utf-8")
    assert secret not in serialized
    record = json.loads(serialized)
    assert record["measurement"]["failure_detail"] == "1 requests failed; first error: redacted"
    assert measurement.failure_detail is not None and secret in measurement.failure_detail

    safe_measurement = measurement.model_copy(
        update={"failure_detail": "1 requests failed; first error: HTTP 503"}
    )
    safe_path = tmp_path / "safe.json"
    save_result(safe_path, spec, safe_measurement, calculate_metrics(safe_measurement))
    assert json.loads(safe_path.read_text())["measurement"]["failure_detail"].endswith("HTTP 503")
