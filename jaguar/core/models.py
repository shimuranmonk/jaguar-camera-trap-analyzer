from dataclasses import dataclass
from enum import StrEnum


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    created_at: str


@dataclass(frozen=True)
class Observation:
    field: str
    source: str
    raw_value: str
    parsed_value: str | None
    method_version: str
    confidence: float | None = None

    def __post_init__(self):
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("Confidence must be between zero and one.")
