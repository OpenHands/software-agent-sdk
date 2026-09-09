"""OpenHands completion log adapter."""

import json
from typing import Any

from flight_recorder.collector.normalize import make_record
from flight_recorder.models.envelopes import Record

from openhands.sdk.conversation.conversation_stats import ConversationStats


def normalize_completion_log(
    filename: str, log_data: str, *, producer_id: str, trace_id: str
) -> Record:
    payload: dict[str, Any] = json.loads(log_data)
    response_id = payload.get("response_id") or payload.get("id")
    return make_record(
        producer_id=producer_id,
        trace_id=trace_id,
        kind="llm.response",
        payload={"filename": filename, "completion": payload},
        llm_response_id=response_id,
    )


def normalize_metrics(
    stats: ConversationStats,
    *,
    producer_id: str,
    trace_id: str,
    agent_span_id: str | None = None,
) -> Record:
    return make_record(
        producer_id=producer_id,
        trace_id=trace_id,
        kind="metrics.snapshot",
        payload=stats.model_dump(mode="json", context={"use_snapshot": True}),
        span_id=agent_span_id,
        agent_span_id=agent_span_id,
    )
