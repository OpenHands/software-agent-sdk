"""Preserve the existing status contract while clients adopt PAUSING.

Keep this projection at network boundaries: internal state and persisted events
must retain PAUSING. Removal needs an announced five-minor-release migration
window; the release containing the notice determines its start, not this branch.
"""

from typing import Annotated, Any

from pydantic import PlainSerializer, WithJsonSchema

from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import Event
from openhands.sdk.event.conversation_state import (
    FULL_STATE_KEY,
    ConversationStateUpdateEvent,
)


def execution_status_for_api(status: str) -> str:
    """Represent a pending pause as active work for existing clients."""
    if status == ConversationExecutionStatus.PAUSING:
        return ConversationExecutionStatus.RUNNING.value
    return status


ConversationAPIExecutionStatus = Annotated[
    ConversationExecutionStatus,
    PlainSerializer(execution_status_for_api, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "enum": [
                status.value
                for status in ConversationExecutionStatus
                if status != ConversationExecutionStatus.PAUSING
            ],
        },
        mode="serialization",
    ),
]


def _state_update_value_for_api(key: str, value: Any) -> Any:
    if key == "execution_status" and isinstance(value, str):
        return execution_status_for_api(value)
    if (
        key == FULL_STATE_KEY
        and isinstance(value, dict)
        and value.get("execution_status") == ConversationExecutionStatus.PAUSING
    ):
        return {**value, "execution_status": ConversationExecutionStatus.RUNNING.value}
    return value


def conversation_event_for_api(event: Event) -> Event:
    """Project state updates without modifying stored or shared event objects."""
    if not isinstance(event, ConversationStateUpdateEvent):
        return event
    value = _state_update_value_for_api(event.key, event.value)
    if value is event.value:
        return event
    return event.model_copy(update={"value": value})


def conversation_event_dict_for_api(event: dict[str, Any]) -> dict[str, Any]:
    """Apply the same projection to raw events returned by event searches."""
    if event.get("kind") != "ConversationStateUpdateEvent":
        return event
    value = _state_update_value_for_api(event.get("key", ""), event.get("value"))
    if value is event.get("value"):
        return event
    return {**event, "value": value}
