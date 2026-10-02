from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
    ]
    url: HttpUrl
    requests: int = Field(gt=0, strict=True)
    concurrency: int = Field(default=1, gt=0, strict=True)


def load_experiment(path: Path) -> ExperimentSpec:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)

    if not isinstance(data, dict):
        raise TypeError(f"Experiment config must be a YAML mapping: {path}")
    return ExperimentSpec.model_validate(data)
