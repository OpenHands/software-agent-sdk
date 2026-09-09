"""OpenHands LLM telemetry adapters."""

import json
import math
from datetime import datetime, timedelta
from typing import Any

from flight_recorder.collector.normalize import make_record
from flight_recorder.models.envelopes import Record

from openhands.sdk.conversation.conversation_stats import ConversationStats


_RESPONSE_FIELDS = {
    "response",
    "response_id",
    "id",
    "raw_response",
    "error",
    "usage_summary",
    "cost",
    "timestamp",
    "latency_sec",
}
_LOG_METADATA_FIELDS = {"llm_call_id", "timestamp"}


def _completion_times(
    payload: dict[str, Any],
) -> tuple[datetime | None, datetime | None]:
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int | float):
        return None, None
    timestamp = float(timestamp)
    if not math.isfinite(timestamp):
        return None, None
    try:
        response_time = datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return None, None
    latency = payload.get("latency_sec")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, int | float)
        or not math.isfinite(latency)
        or latency < 0
    ):
        return response_time, response_time
    return response_time - timedelta(seconds=latency), response_time


def normalize_request_log(
    llm_call_id: str, log_data: str, *, producer_id: str, trace_id: str
) -> Record:
    payload: dict[str, Any] = json.loads(log_data)
    request_payload = {
        key: value for key, value in payload.items() if key not in _LOG_METADATA_FIELDS
    }
    request_time, _ = _completion_times(payload)
    return make_record(
        producer_id=producer_id,
        trace_id=trace_id,
        kind="llm.request",
        payload={"request": request_payload},
        llm_call_id=llm_call_id,
        source_timestamp=request_time,
    )


def normalize_completion_log(
    filename: str, log_data: str, *, producer_id: str, trace_id: str
) -> Record:
    payload: dict[str, Any] = json.loads(log_data)
    response = payload.get("response")
    response_id = response.get("id") if isinstance(response, dict) else None
    response_id = response_id or payload.get("response_id") or payload.get("id")
    response_payload = {
        key: value for key, value in payload.items() if key in _RESPONSE_FIELDS
    }
    _, response_time = _completion_times(payload)
    call_id = payload.get("llm_call_id")
    return make_record(
        producer_id=producer_id,
        trace_id=trace_id,
        kind="llm.response",
        payload={"filename": filename, **response_payload},
        llm_call_id=call_id if isinstance(call_id, str) else None,
        llm_response_id=response_id,
        source_timestamp=response_time,
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
