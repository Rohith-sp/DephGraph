from datetime import UTC, datetime, timedelta

from pydantic import AwareDatetime, BaseModel, Field, field_validator

MAX_SAMPLES = 5000
CLOCK_SKEW = timedelta(minutes=5)


class RuntimeSample(BaseModel):
    """One package seen by the runtime agent: how often its code ran in the capture window."""

    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=100)
    exec_count: int = Field(ge=0, le=2_147_483_647)  # observes.exec_count is INT


class RuntimeSamplesRequest(BaseModel):
    service_id: int
    deploy_seq: int | None = Field(default=None, ge=1)  # None = latest deployment
    captured_at: AwareDatetime | None = None  # None = now
    # True: `samples` is everything the agent saw, so resolved packages missing from it are
    # recorded with exec_count 0 (declared but never executed in this window).
    complete: bool = False
    samples: list[RuntimeSample] = Field(min_length=1, max_length=MAX_SAMPLES)

    @field_validator("captured_at")
    @classmethod
    def not_in_future(cls, v: datetime | None) -> datetime | None:
        if v is not None and v > datetime.now(UTC) + CLOCK_SKEW:
            raise ValueError("captured_at is in the future")
        return v


class SkippedSample(BaseModel):
    name: str
    version: str


class RuntimeSamplesResult(BaseModel):
    scan_id: int
    deploy_seq: int
    samples_received: int
    observes_written: int
    live_packages: int
    samples_skipped: int
    skipped: list[SkippedSample]  # first 50 only
    raw_stored: bool  # False when MongoDB was unreachable (projection still happened)
