"""Conversation event wire-format helpers for agent-server APIs."""

from __future__ import annotations

from typing import Any

from openhands.sdk.event import Event


_STABLE_TOOL_KINDS = {
    "ClientTool",
    "FileEditorTool",
    "FinishTool",
    "InvokeSkillTool",
    "MCPToolDefinition",
    "SwitchLLMTool",
    "TaskTool",
    "TaskToolSet",
    "TaskTrackerTool",
    "TerminalTool",
    "ThinkTool",
    "WorkflowTool",
    "WorkflowToolSet",
}


def _filter_stable_tool_definitions(tools: Any) -> Any:
    if not isinstance(tools, list):
        return tools
    return [
        tool
        for tool in tools
        if not isinstance(tool, dict) or tool.get("kind") in _STABLE_TOOL_KINDS
    ]


def _filter_state_update_tools(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("tools"), list):
        payload["tools"] = _filter_stable_tool_definitions(payload["tools"])

    value = payload.get("value")
    if isinstance(value, dict) and isinstance(value.get("tools"), list):
        value = dict(value)
        value["tools"] = _filter_stable_tool_definitions(value["tools"])
        payload["value"] = value
    elif payload.get("key") == "tools" and isinstance(value, list):
        payload["value"] = _filter_stable_tool_definitions(value)

    return payload


def dump_conversation_event_for_wire(event: Event) -> dict[str, Any]:
    """Serialize the stable conversation-event API contract.

    ``parent_id`` and newly added tool-definition kinds are internal/newer SDK
    fields. Sending them on existing unversioned event APIs breaks older SDK
    clients whose Pydantic models forbid unknown fields.
    """
    payload = event.model_dump(mode="json", exclude={"parent_id"})
    return _filter_state_update_tools(payload)
