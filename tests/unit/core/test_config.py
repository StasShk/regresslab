import pytest
from pydantic import ValidationError

from regresslab.core.config import ExperimentSpec


@pytest.fixture
def valid_config() -> dict[str, object]:
    return {
        "name": "health-check",
        "url": "http://localhost:8000/health",
        "requests": 100,
    }


def test_valid_config(valid_config):
    spec = ExperimentSpec.model_validate(valid_config)

    assert spec.name == "health-check"
    assert str(spec.url) == "http://localhost:8000/health"
    assert spec.requests == 100
    assert spec.concurrency == 1


def test_explicit_concurrency(valid_config):
    spec = ExperimentSpec.model_validate({**valid_config, "concurrency": 5})

    assert spec.concurrency == 5


def test_strips_name_whitespace(valid_config):
    spec = ExperimentSpec.model_validate({**valid_config, "name": "  health-check \t"})

    assert spec.name == "health-check"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_rejects_empty_name(valid_config, name):
    with pytest.raises(ValidationError) as exc:
        ExperimentSpec.model_validate({**valid_config, "name": name})

    assert exc.value.errors()[0]["loc"] == ("name",)


def test_accepts_https(valid_config):
    spec = ExperimentSpec.model_validate({**valid_config, "url": "https://example.com/health"})

    assert str(spec.url) == "https://example.com/health"


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com", "/health"])
def test_rejects_invalid_url(valid_config, url):
    with pytest.raises(ValidationError) as exc:
        ExperimentSpec.model_validate({**valid_config, "url": url})

    assert exc.value.errors()[0]["loc"] == ("url",)


@pytest.mark.parametrize("field", ["requests", "concurrency"])
@pytest.mark.parametrize("value", [0, -1, 1.5, 1.0, True, "10"])
def test_rejects_invalid_counts(valid_config, field, value):
    with pytest.raises(ValidationError) as exc:
        ExperimentSpec.model_validate({**valid_config, field: value})

    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", ["name", "url", "requests"])
def test_requires_mandatory_fields(valid_config, field):
    del valid_config[field]

    with pytest.raises(ValidationError) as exc:
        ExperimentSpec.model_validate(valid_config)

    error = exc.value.errors()[0]
    assert error["loc"] == (field,)
    assert error["type"] == "missing"


def test_rejects_unknown_fields(valid_config):
    with pytest.raises(ValidationError) as exc:
        ExperimentSpec.model_validate({**valid_config, "concurency": 5})

    error = exc.value.errors()[0]
    assert error["loc"] == ("concurency",)
    assert error["type"] == "extra_forbidden"


def test_config_is_frozen(valid_config):
    spec = ExperimentSpec.model_validate(valid_config)

    with pytest.raises(ValidationError) as exc:
        spec.concurrency = 2

    assert exc.value.errors()[0]["type"] == "frozen_instance"
