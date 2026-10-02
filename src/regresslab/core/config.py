from typing import Annotated

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