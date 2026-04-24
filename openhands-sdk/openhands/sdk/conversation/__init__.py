from __future__ import annotations

from typing import Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


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
