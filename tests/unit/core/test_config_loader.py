from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from regresslab.core.config import load_experiment

VALID_YAML = """\
name: health-check
url: http://localhost:8000/health
requests: 100
concurrency: 5
"""


def test_loads_valid_config(tmp_path: Path):
    path = tmp_path / "experiment.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")

    spec = load_experiment(path)

    assert spec.name == "health-check"
    assert str(spec.url) == "http://localhost:8000/health"
    assert spec.requests == 100
    assert spec.concurrency == 5


@pytest.mark.parametrize(
    "content",
    ["", "# only a comment\n", "- item\n", "42\n", "null\n"],
)
def test_rejects_non_mapping_yaml(tmp_path: Path, content: str):
    path = tmp_path / "experiment.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(TypeError, match="must be a YAML mapping"):
        load_experiment(path)


def test_rejects_malformed_yaml(tmp_path: Path):
    path = tmp_path / "experiment.yaml"
    path.write_text("requests: [100\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_experiment(path)


def test_rejects_invalid_parameters(tmp_path: Path):
    path = tmp_path / "experiment.yaml"
    path.write_text(
        VALID_YAML.replace("requests: 100", "requests: 0"),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc:
        load_experiment(path)

    assert exc.value.errors()[0]["loc"] == ("requests",)


def test_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_experiment(tmp_path / "missing.yaml")
