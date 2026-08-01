"""
OpenHands Hooks System - Event-driven hooks for automation and control.

Hooks are event-driven scripts that execute at specific lifecycle events
during agent execution, enabling deterministic control over agent behavior.
"""

from openhands.sdk.hooks.config import (
    HOOK_CONFIG_DIRS,
    HOOK_CONFIG_FILENAME,
    HOOK_EVENT_FIELDS,
    HookConfig,
    HookDefinition,
    HookMatcher,
    HookType,
    find_hooks_file,
)
from openhands.sdk.hooks.conversation_hooks import (
    HookEventProcessor,
    create_hook_callback,
)
from openhands.sdk.hooks.executor import HookExecutor, HookResult
from openhands.sdk.hooks.manager import HookManager
from openhands.sdk.hooks.types import HookDecision, HookEvent, HookEventType


__all__ = [
    "HOOK_CONFIG_DIRS",
    "HOOK_CONFIG_FILENAME",
    "HOOK_EVENT_FIELDS",
    "HookConfig",
    "HookDefinition",
    "HookMatcher",
    "HookType",
    "HookExecutor",
    "HookResult",
    "HookManager",
    "HookEvent",
    "HookEventType",
    "HookDecision",
    "HookEventProcessor",
    "create_hook_callback",
    "find_hooks_file",
]
