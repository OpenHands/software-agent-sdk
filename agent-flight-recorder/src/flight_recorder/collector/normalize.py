"""Normalize SDK values into recorder records."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from flight_recorder.models.envelopes import Provenance, Record


def make_record(
    *,
    producer_id: str,
    trace_id: str,
    kind: str,
    payload: dict[str, Any],
    span_id: str | None = None,
    parent_span_id: str | None = None,
    agent_span_id: str | None = None,
    openhands_event_id: str | None = None,
    llm_response_id: str | None = None,
    source_timestamp: datetime | None = None,
) -> Record:
    return Record(
        record_id=str(uuid4()),
        producer_id=producer_id,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_span_id=agent_span_id,
        kind=kind,
        openhands_event_id=openhands_event_id,
        llm_response_id=llm_response_id,
        source_timestamp=source_timestamp,
        payload=payload,
        provenance=Provenance(),
    )


def condensation_payload(event: Any) -> dict[str, Any]:
    return {
        "forgotten_event_ids": sorted(event.forgotten_event_ids),
        "summary": event.summary,
        "summary_offset": event.summary_offset,
        "llm_response_id": event.llm_response_id,
    }
