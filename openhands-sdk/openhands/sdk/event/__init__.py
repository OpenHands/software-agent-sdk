from __future__ import annotations

from typing import Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


__all__ = [
    "ACPToolCallEvent",
    "Event",
    "LLMConvertibleEvent",
    "SystemPromptEvent",
    "ActionEvent",
    "TokenEvent",
    "ObservationEvent",
    "ObservationBaseEvent",
    "MessageEvent",
    "AgentErrorEvent",
    "UserRejectObservation",
    "RejectionSource",
    "PauseEvent",
    "StreamingDeltaEvent",
    "Condensation",
    "CondensationRequest",
    "CondensationSummaryEvent",
    "ConversationStateUpdateEvent",
    "HookExecutionEvent",
    "LLMCompletionLogEvent",
    "EventID",
    "ToolCallID",
]

_LAZY_IMPORTS = {
    "ACPToolCallEvent": (".acp_tool_call", "ACPToolCallEvent"),
    "Event": (".base", "Event"),
    "LLMConvertibleEvent": (".base", "LLMConvertibleEvent"),
    "SystemPromptEvent": (".llm_convertible", "SystemPromptEvent"),
    "ActionEvent": (".llm_convertible", "ActionEvent"),
    "TokenEvent": (".token", "TokenEvent"),
    "ObservationEvent": (".llm_convertible", "ObservationEvent"),
    "ObservationBaseEvent": (".llm_convertible", "ObservationBaseEvent"),
    "MessageEvent": (".llm_convertible", "MessageEvent"),
    "AgentErrorEvent": (".llm_convertible", "AgentErrorEvent"),
    "UserRejectObservation": (".llm_convertible", "UserRejectObservation"),
    "RejectionSource": (".llm_convertible", "RejectionSource"),
    "PauseEvent": (".user_action", "PauseEvent"),
    "StreamingDeltaEvent": (".streaming_delta", "StreamingDeltaEvent"),
    "Condensation": (".condenser", "Condensation"),
    "CondensationRequest": (".condenser", "CondensationRequest"),
    "CondensationSummaryEvent": (".condenser", "CondensationSummaryEvent"),
    "ConversationStateUpdateEvent": (
        ".conversation_state",
        "ConversationStateUpdateEvent",
    ),
    "HookExecutionEvent": (".hook_execution", "HookExecutionEvent"),
    "LLMCompletionLogEvent": (".llm_completion_log", "LLMCompletionLogEvent"),
    "EventID": (".types", "EventID"),
    "ToolCallID": (".types", "ToolCallID"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
