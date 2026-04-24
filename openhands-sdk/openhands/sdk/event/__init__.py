from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .acp_tool_call import ACPToolCallEvent
    from .base import Event, LLMConvertibleEvent
    from .condenser import (
        Condensation,
        CondensationRequest,
        CondensationSummaryEvent,
    )
    from .conversation_state import ConversationStateUpdateEvent
    from .hook_execution import HookExecutionEvent
    from .llm_completion_log import LLMCompletionLogEvent
    from .llm_convertible import (
        ActionEvent,
        AgentErrorEvent,
        MessageEvent,
        ObservationBaseEvent,
        ObservationEvent,
        RejectionSource,
        SystemPromptEvent,
        UserRejectObservation,
    )
    from .streaming_delta import StreamingDeltaEvent
    from .token import TokenEvent
    from .types import EventID, ToolCallID
    from .user_action import PauseEvent


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
