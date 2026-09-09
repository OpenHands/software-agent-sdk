"""Immutable query results consumed by CLI and GUI clients."""

from datetime import datetime
from typing import Any

from flight_recorder.models.envelopes import (
    Finding,
    Provenance,
    Record,
    RelationshipStatus,
    Span,
    TokenUsage,
)
from pydantic import BaseModel, ConfigDict, Field


class ViewModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class RunQuery(ViewModel):
    status: str | None = None
    model: str | None = None
    repository: str | None = None


class RunSummary(ViewModel):
    trace_id: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    record_count: int = 0
    finding_count: int = 0
    total_usage: TokenUsage = Field(default_factory=TokenUsage)
    total_cost: float = Field(default=0, ge=0)


class RunDetail(ViewModel):
    summary: RunSummary
    spans: tuple[Span, ...] = ()
    findings: tuple[Finding, ...] = ()


class TimelineView(ViewModel):
    trace_id: str
    records: tuple[Record, ...]
    spans: tuple[Span, ...] = ()


class SpanDetail(ViewModel):
    span: Span
    records: tuple[Record, ...]


class Selection(ViewModel):
    record_id: str | None = None
    span_id: str | None = None


class ContextView(ViewModel):
    record_id: str
    messages: tuple[dict[str, Any], ...]
    provenance: Provenance


class CondensationView(ViewModel):
    resolved_ids: tuple[str, ...]
    unresolved_ids: tuple[str, ...]
    summary: str | None = None


class AgentRelationship(ViewModel):
    parent_span_id: str | None
    child_span_id: str
    status: RelationshipStatus
    provenance: Provenance


class AgentDetail(ViewModel):
    relationship: AgentRelationship
    handoff: dict[str, Any] = Field(default_factory=dict)
    returned_result: Any = None
    duration_seconds: float | None = Field(default=None, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: float = Field(default=0, ge=0)
