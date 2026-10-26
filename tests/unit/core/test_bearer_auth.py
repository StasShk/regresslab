import asyncio

import httpx
import pytest
from pydantic import ValidationError

from regresslab.core import http_runner
from regresslab.core.config import ExperimentSpec
from regresslab.core.provenance import configuration_fingerprint
from regresslab.core.saved_results import validate_experiment_compatibility


def spec(**changes):
    return ExperimentSpec.model_validate(
        {
            "name": "api",
            "url": "http://localhost/health",
            "requests": 2,
            "warmup_requests": 1,
            "bearer_token_env": "BENCH_TOKEN",
            **changes,
        }
    )


@pytest.mark.parametrize("name", ["", "bad-name", "1TOKEN", "TOKEN\n", True])
def test_rejects_invalid_environment_name(name):
    with pytest.raises(ValidationError):
        spec(bearer_token_env=name)


@pytest.mark.parametrize("token", [None, "", "bad token", "secret\r\nX: injected", "секрет"])
def test_invalid_token_fails_before_client_creation(monkeypatch, token):
    if token is None:
        monkeypatch.delenv("BENCH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BENCH_TOKEN", token)

    def unexpected_client(**kwargs):
        pytest.fail("Invalid credentials must not create an HTTP client")

    monkeypatch.setattr(http_runner.httpx, "AsyncClient", unexpected_client)
    with pytest.raises(ValueError, match="bearer_token_env") as error:
        asyncio.run(http_runner.run_experiment(spec()))
    if token:
        assert token not in str(error.value)


def test_warmup_and_measurement_send_bearer_without_following_redirect(monkeypatch):
    token = "private-token.abc+/=="
    monkeypatch.setenv("BENCH_TOKEN", token)
    observed = []

    def handler(request):
        observed.append((str(request.url), request.headers.get("Authorization")))
        return httpx.Response(302, headers={"Location": "http://other.invalid/"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        http_runner.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = asyncio.run(http_runner.run_experiment(spec()))
    assert observed == [("http://localhost/health", f"Bearer {token}")] * 3
    assert result.request_count == result.failed_requests == 2
    assert token not in result.model_dump_json()


def test_auth_mode_is_compatible_across_environment_names():
    before, after = spec(), spec(bearer_token_env="OTHER_TOKEN")
    validate_experiment_compatibility(before, after)
    assert configuration_fingerprint(before) == configuration_fingerprint(after)
    anonymous = spec(bearer_token_env=None)
    assert configuration_fingerprint(before) != configuration_fingerprint(anonymous)
    with pytest.raises(ValueError, match="authentication"):
        validate_experiment_compatibility(before, anonymous)


def test_bearer_rejects_url_credentials():
    with pytest.raises(ValidationError):
        spec(url="http://user:password@localhost/health")


def test_transport_error_does_not_echo_token(monkeypatch):
    token = "secret-token-in-exception"
    monkeypatch.setenv("BENCH_TOKEN", token)

    def handler(request):
        raise httpx.ReadTimeout(f"failed Authorization: Bearer {token}", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        http_runner.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = asyncio.run(http_runner.run_experiment(spec()))
    assert result.failed_requests == 2
    assert "ReadTimeout" in result.failure_detail
    assert token not in result.model_dump_json()


def test_token_rotation_does_not_change_fingerprint(monkeypatch):
    experiment = spec()
    monkeypatch.setenv("BENCH_TOKEN", "first-private-token")
    original = configuration_fingerprint(experiment)
    monkeypatch.setenv("BENCH_TOKEN", "second-private-token")
    assert configuration_fingerprint(experiment) == original
    assert "private-token" not in experiment.model_dump_json()
