from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from regresslab.core.provenance import EnvironmentMetadata


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
    ]
    url: HttpUrl
    requests: int = Field(gt=0, strict=True)
    concurrency: int = Field(default=1, gt=0, strict=True)
    timeout_seconds: float = Field(default=5.0, gt=0, strict=True, allow_inf_nan=False)
    bearer_token_env: str | None = Field(
        default=None, strict=True, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    warmup_requests: int = Field(default=0, ge=0, strict=True)
    max_duration_seconds: float | None = Field(default=None, gt=0, strict=True, allow_inf_nan=False)
    max_requests: int | None = Field(default=None, gt=0, strict=True)
    max_concurrency: int | None = Field(default=None, gt=0, strict=True)
    max_result_bytes: int | None = Field(default=None, gt=0, strict=True)
    # Stored at result top-level, never duplicated in the experiment workload.
    environment: EnvironmentMetadata | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def enforce_execution_budgets(self) -> Self:
        if self.bearer_token_env is not None and (self.url.username or self.url.password):
            raise ValueError("bearer_token_env cannot be combined with URL credentials")
        # Count all outgoing requests, including those excluded from metrics.
        if (
            self.max_requests is not None
            and self.requests + self.warmup_requests > self.max_requests
        ):
            raise ValueError("requests + warmup_requests exceeds max_requests")
        if self.max_concurrency is not None and self.concurrency > self.max_concurrency:
            raise ValueError("concurrency exceeds max_concurrency")
        return self


def load_experiment(path: Path) -> ExperimentSpec:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)

    if not isinstance(data, dict):
        raise TypeError(f"Experiment config must be a YAML mapping: {path}")
    return ExperimentSpec.model_validate(data)
