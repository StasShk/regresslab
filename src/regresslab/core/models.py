from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeFloat = Annotated[float, Field(ge=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class MeasurementStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class Measurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    latencies_ms: tuple[NonNegativeFloat, ...]
    request_count: NonNegativeInt
    successful_requests: NonNegativeInt
    failed_requests: NonNegativeInt
    elapsed_seconds: NonNegativeFloat
    status: MeasurementStatus
    failure_detail: str | None = None

    @model_validator(mode="after")
    def validate_counters(self) -> Self:
        if self.request_count != self.successful_requests + self.failed_requests:
            raise ValueError("request_count must equal successful_requests + failed_requests")

        if self.status is MeasurementStatus.SUCCESS and self.elapsed_seconds == 0:
            raise ValueError("successful measurement requires elapsed_seconds > 0")
        return self

    @property
    def throughput_rps(self) -> float:
        if self.status is MeasurementStatus.FAILED:
            return 0.0

        return self.successful_requests / self.elapsed_seconds
