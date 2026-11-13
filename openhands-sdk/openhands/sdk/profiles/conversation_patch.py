"""Validate conversation-scoped edits to a saved agent profile's launch choices."""

from collections.abc import Mapping
from typing import Any

from openhands.sdk.profiles.agent_profile import (
    OpenHandsAgentProfile,
    validate_agent_profile,
)
from openhands.sdk.settings.model import _merge_patch


EDITABLE_FIELDS = frozenset(
    {"condenser", "mcp_server_refs", "mcp_tool_policy", "secret_refs"}
)


def apply_conversation_profile_diff(
    profile: OpenHandsAgentProfile, diff: Mapping[str, Any]
) -> OpenHandsAgentProfile:
    """Apply a sparse edit without changing the profile's identity or global store."""
    unsupported = set(diff) - EDITABLE_FIELDS
    if unsupported:
        raise ValueError(
            f"Unsupported conversation profile fields: {sorted(unsupported)}"
        )
    payload = _merge_patch(profile.model_dump(mode="json"), diff)
    result = validate_agent_profile(payload)
    if not isinstance(result, OpenHandsAgentProfile):
        raise ValueError("Only OpenHands agent profiles support live edits")
    return result
