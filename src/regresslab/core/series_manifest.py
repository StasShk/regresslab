"""Validate YAML manifests of previously saved benchmark pairs."""

from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from regresslab.core.validation_errors import safe_cli_exception, safe_validation_details

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SavedResultPair(BaseModel):
    """Paths are resolved relative to the YAML manifest, not the working directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: NonEmptyText
    baseline: NonEmptyText
    candidate: NonEmptyText


class SeriesManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pairs: tuple[SavedResultPair, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_pair_ids(self) -> Self:
        identifiers = [pair.id for pair in self.pairs]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("pair IDs must be unique")
        return self


def load_series_manifest(path: Path) -> SeriesManifest:
    """Load a series manifest, retaining the order of its explicit pairs."""
    try:
        with path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid series manifest: {safe_cli_exception(exc)}") from exc
    try:
        return SeriesManifest.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"{path}: invalid series manifest: {safe_validation_details(exc)}"
        ) from exc
