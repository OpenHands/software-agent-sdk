"""Canonical recorder records and derived domain entities."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class ProvenanceKind(StrEnum):
    CAPTURED = "captured"
    RECONSTRUCTED = "reconstructed"
    DERIVED = "derived"
    INTERPRETED = "interpreted"


class RelationshipStatus(StrEnum):
    VERIFIED = "verified"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class Provenance(FrozenModel):
    kind: ProvenanceKind = ProvenanceKind.CAPTURED
    source_ids: tuple[str, ...] = ()
    description: str | None = None
    complete: bool = True


class Record(FrozenModel):
    schema_version: int = Field(default=1, ge=1)
    record_id: str
    producer_id: str
    trace_id: str
    span_id: str | None = None
    parent_span_id: str | None = None
    agent_span_id: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    observed_at: datetime = Field(default_factory=datetime.now)
    source_timestamp: datetime | None = None
    monotonic_ns: int | None = Field(default=None, ge=0)
    kind: str
    openhands_event_id: str | None = None
    llm_response_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    content_ref: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    provenance: Provenance = Field(default_factory=Provenance)


class Trace(FrozenModel):
    trace_id: str
    format_version: int = Field(default=1, ge=1)
    status: RunStatus = RunStatus.CREATED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stop_reason: str | None = None
    recorder_version: str = "0.1.0"
    record_count: int | None = Field(default=None, ge=0)
    finding_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "Trace":
        terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED}
        if self.status in terminal and (
            self.completed_at is None or not self.stop_reason
        ):
            raise ValueError("terminal traces require completed_at and stop_reason")
        return self


class Span(FrozenModel):
    span_id: str
    trace_id: str
    span_type: str
    name: str = Field(min_length=1)
    parent_span_id: str | None = None
    agent_span_id: str | None = None
    relationship_status: RelationshipStatus = RelationshipStatus.VERIFIED
    start_sequence: int = Field(ge=1)
    end_sequence: int | None = Field(default=None, ge=1)
    started_at: datetime
    ended_at: datetime | None = None
    status: str = "running"
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_range(self) -> "Span":
        if self.end_sequence is not None and self.end_sequence < self.start_sequence:
            raise ValueError("end_sequence precedes start_sequence")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at precedes started_at")
        return self


class TokenUsage(FrozenModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class Artifact(FrozenModel):
    artifact_id: str
    trace_id: str
    span_id: str | None = None
    kind: str
    path: str | None = None
    content_hash: str | None = None
    size: int | None = Field(default=None, ge=0)
    created_sequence: int = Field(ge=1)
    content_ref: str | None = None


class Finding(FrozenModel):
    finding_id: str
    trace_id: str
    origin: Literal["rule", "model"]
    detector_id: str
    category: str
    severity: Literal["info", "warning", "error"]
    confidence: float = Field(ge=0, le=1)
    title: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    affected_span_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=datetime.now)


class CommittedBatch(FrozenModel):
    trace_id: str
    first_sequence: int = Field(ge=1)
    last_sequence: int = Field(ge=1)
    record_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_range(self) -> "CommittedBatch":
        if self.last_sequence < self.first_sequence:
            raise ValueError("last_sequence precedes first_sequence")
        return self
