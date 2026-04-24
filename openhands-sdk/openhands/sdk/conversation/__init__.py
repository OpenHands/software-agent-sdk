from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .base import BaseConversation
    from .conversation import Conversation
    from .event_store import EventLog
    from .events_list_base import EventsListBase
    from .exceptions import WebSocketConnectionError
    from .impl.local_conversation import LocalConversation
    from .impl.remote_conversation import RemoteConversation
    from .resource_lock_manager import ResourceLockManager, ResourceLockTimeout
    from .response_utils import get_agent_final_response
    from .secret_registry import SecretRegistry
    from .state import ConversationExecutionStatus, ConversationState
    from .stuck_detector import StuckDetector
    from .types import (
        ConversationCallbackType,
        ConversationTags,
        ConversationTokenCallbackType,
    )
    from .visualizer import (
        ConversationVisualizerBase,
        DefaultConversationVisualizer,
    )


__all__ = [
    "Conversation",
    "BaseConversation",
    "ConversationState",
    "ConversationExecutionStatus",
    "ConversationCallbackType",
    "ConversationTags",
    "ConversationTokenCallbackType",
    "DefaultConversationVisualizer",
    "ConversationVisualizerBase",
    "SecretRegistry",
    "StuckDetector",
    "EventLog",
    "ResourceLockManager",
    "ResourceLockTimeout",
    "LocalConversation",
    "RemoteConversation",
    "EventsListBase",
    "get_agent_final_response",
    "WebSocketConnectionError",
]

_LAZY_IMPORTS = {
    "Conversation": (".conversation", "Conversation"),
    "BaseConversation": (".base", "BaseConversation"),
    "ConversationState": (".state", "ConversationState"),
    "ConversationExecutionStatus": (".state", "ConversationExecutionStatus"),
    "ConversationCallbackType": (".types", "ConversationCallbackType"),
    "ConversationTags": (".types", "ConversationTags"),
    "ConversationTokenCallbackType": (".types", "ConversationTokenCallbackType"),
    "DefaultConversationVisualizer": (
        ".visualizer",
        "DefaultConversationVisualizer",
    ),
    "ConversationVisualizerBase": (".visualizer", "ConversationVisualizerBase"),
    "SecretRegistry": (".secret_registry", "SecretRegistry"),
    "StuckDetector": (".stuck_detector", "StuckDetector"),
    "EventLog": (".event_store", "EventLog"),
    "ResourceLockManager": (".resource_lock_manager", "ResourceLockManager"),
    "ResourceLockTimeout": (".resource_lock_manager", "ResourceLockTimeout"),
    "LocalConversation": (".impl.local_conversation", "LocalConversation"),
    "RemoteConversation": (".impl.remote_conversation", "RemoteConversation"),
    "EventsListBase": (".events_list_base", "EventsListBase"),
    "get_agent_final_response": (".response_utils", "get_agent_final_response"),
    "WebSocketConnectionError": (".exceptions", "WebSocketConnectionError"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
