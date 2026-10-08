from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeFloat = Annotated[float, Field(ge=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class RequestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    latency_ms: NonNegativeFloat | None
    succeeded: bool
    failure_detail: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.succeeded and self.latency_ms is None:
            raise ValueError("successful request requires latency_ms")
        if not self.succeeded and self.latency_ms is not None:
            raise ValueError("failed request must not have latency_ms")
        if self.succeeded and self.failure_detail is not None:
            raise ValueError("successful request must not have failure_detail")
        return self


class Measurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    latencies_ms: tuple[NonNegativeFloat, ...]
    request_count: NonNegativeInt
    successful_requests: NonNegativeInt
    failed_requests: NonNegativeInt
    elapsed_seconds: NonNegativeFloat
    failure_detail: str | None = None

    @model_validator(mode="after")
    def validate_counters(self) -> Self:
        if self.request_count != self.successful_requests + self.failed_requests:
            raise ValueError("request_count must equal successful_requests + failed_requests")

        if len(self.latencies_ms) != self.successful_requests:
            raise ValueError("latencies_ms must contain one entry per successful request")

        if self.request_count > 0 and self.elapsed_seconds == 0:
            raise ValueError("measurement with requests requires elapsed_seconds > 0")
        return self

    @property
    def successful_throughput_rps(self) -> float:
        """Successful requests per second, excluding failed requests."""
        if self.elapsed_seconds == 0:
            return 0.0
        return self.successful_requests / self.elapsed_seconds
