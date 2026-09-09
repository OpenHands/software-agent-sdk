"""OpenHands conversation event adapter."""

from datetime import datetime
from typing import Any

from flight_recorder.collector.normalize import condensation_payload, make_record
from flight_recorder.models.envelopes import Record

from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import ObservationEvent


def normalize_event(
    event: Any,
    *,
    producer_id: str,
    trace_id: str,
    agent_span_id: str | None = None,
    parent_agent_span_id: str | None = None,
) -> Record:
    event_type = event.__class__.__name__
    payload = event.model_dump(mode="json")
    kind = "conversation.event"
    span_id = agent_span_id
    parent_span_id = parent_agent_span_id
    llm_response_id = None
    if isinstance(event, Condensation):
        kind = "context.condensed"
        payload = condensation_payload(event)
        llm_response_id = event.llm_response_id
    elif isinstance(event, ActionEvent):
        llm_response_id = event.llm_response_id
        if event.tool_name == "task":
            kind = "delegation.started"
            span_id = event.id
            parent_span_id = agent_span_id
    elif isinstance(event, ObservationEvent) and event.tool_name == "task":
        kind = "delegation.finished"
        span_id = event.action_id
        parent_span_id = agent_span_id
    timestamp = datetime.fromisoformat(event.timestamp) if event.timestamp else None
    return make_record(
        producer_id=producer_id,
        trace_id=trace_id,
        kind=kind,
        payload={"event_type": event_type, **payload},
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_span_id=agent_span_id,
        openhands_event_id=event.id,
        llm_response_id=llm_response_id,
        source_timestamp=timestamp,
    )
