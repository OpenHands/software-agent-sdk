from openhands.sdk.event.acp_tool_call import ACPToolCallEvent
from openhands.sdk.event.base import Event, LLMConvertibleEvent
from openhands.sdk.event.condenser import (
    Condensation,
    CondensationRequest,
    CondensationSummaryEvent,
)
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.event.hook_execution import HookExecutionEvent
from openhands.sdk.event.llm_completion_log import LLMCompletionLogEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationBaseEvent,
    ObservationEvent,
    RejectionSource,
    SystemPromptEvent,
    UserRejectObservation,
)
from openhands.sdk.event.token import TokenEvent
from openhands.sdk.event.types import EventID, ToolCallID
from openhands.sdk.event.user_action import PauseEvent


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
    "Condensation",
    "CondensationRequest",
    "CondensationSummaryEvent",
    "ConversationStateUpdateEvent",
    "HookExecutionEvent",
    "LLMCompletionLogEvent",
    "EventID",
    "ToolCallID",
]


# Rebuild SystemPromptEvent model to resolve forward reference to HookConfig
# This must be done after all imports are complete to avoid circular import
def _rebuild_models() -> None:
    # Import from the leaf module to avoid importing the hooks package, which can
    # create circular import chains (hooks -> events -> hooks).
    from openhands.sdk.hooks.config import HookConfig

    SystemPromptEvent.model_rebuild(_types_namespace={"HookConfig": HookConfig})


_rebuild_models()
