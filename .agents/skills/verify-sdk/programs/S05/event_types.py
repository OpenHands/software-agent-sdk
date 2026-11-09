"""Build the control-flow and telemetry events a program meets and round-trip
them through JSON: errors with their classification, streaming deltas and
completion logs.
"""

from sdk_verify import emit

from openhands.sdk.event import (
    AgentErrorEvent,
    Event,
    LLMCompletionLogEvent,
    StreamingDeltaEvent,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.error_classification import FailureKind, classify_error


def roundtrip(event: Event) -> bool:
    text = event.model_dump_json()
    back = Event.model_validate_json(text)
    return type(back) is type(event) and back == event


secret = "sk-very-secret-value"
conversation_error = ConversationErrorEvent(
    source="environment",
    code="AuthenticationError",
    detail=f"invalid api key {secret}",
)
classified = conversation_error  # classified when it is built
agent_error = AgentErrorEvent(
    error="File not found: missing.txt", tool_name="file_editor", tool_call_id="c1"
)
delta = StreamingDeltaEvent(content="Hel", reasoning_content="thinking")
completion_log = LLMCompletionLogEvent(
    filename="log-1.json",
    log_data='{"model": "m"}',
    model_name="m",
    usage_id="agent",
)

emit(
    {
        "ok": True,
        "kinds": {
            "conversation_error": conversation_error.model_dump()["kind"],
            "streaming_delta": delta.model_dump()["kind"],
            "completion_log": completion_log.model_dump()["kind"],
        },
        "roundtrip": {
            "conversation_error": roundtrip(classified),
            "agent_error": roundtrip(agent_error),
            "streaming_delta": roundtrip(delta),
            "completion_log": roundtrip(completion_log),
        },
        "auth_kind": str(classify_error("LLMAuthenticationError", "").kind),
        "rate_limit_kind": str(classify_error("APIError", "Rate limit reached").kind),
        "code_beats_detail": str(
            classify_error("KeyError", "invalid api key in message").kind
        ),
        "agent_error_kind": str(agent_error.classification.kind)
        if agent_error.classification
        else None,
        "conversation_error_kind": str(classified.classification.kind)
        if getattr(classified, "classification", None)
        else None,
        "secret_in_classification": secret
        in (
            classified.classification.model_dump_json()
            if classified.classification
            else ""
        ),
        "failure_kinds": sorted(k.value for k in FailureKind),
    }
)
