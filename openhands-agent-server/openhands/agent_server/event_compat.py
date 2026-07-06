"""Compatibility helpers for event transport payloads."""

from typing import Any, Final

from openhands.sdk.event import Event


_LEGACY_TOOL_KINDS: Final[set[str]] = {
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


def event_transport_dump(event: Event | dict[str, Any]) -> dict[str, Any]:
    """Serialize an event for HTTP/WebSocket clients across SDK versions.

    Some deployed clients run older SDKs than the agent-server. They parse event
    payloads with strict pydantic unions, so additive fields and new tool kinds
    can make otherwise completed conversations look failed client-side. Keep
    persistence untouched and apply this compatibility envelope only at the API
    transport boundary.
    """
    if isinstance(event, dict):
        dumped = dict(event)
    else:
        dumped = event.model_dump(mode="json", exclude_none=True)
    dumped.pop("parent_id", None)

    tools = dumped.get("tools")
    if isinstance(tools, list):
        dumped["tools"] = [
            tool
            for tool in tools
            if not isinstance(tool, dict)
            or tool.get("kind") is None
            or tool.get("kind") in _LEGACY_TOOL_KINDS
        ]

    return dumped
