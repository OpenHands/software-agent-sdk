from unittest.mock import MagicMock

import pytest

from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.tool.client_tool import (
    ClientTool,
    ClientToolRegistrationError,
    ClientToolSpec,
    register_client_tools,
    resolve_client_tool,
)
from openhands.sdk.tool.registry import register_tool, resolve_tool
from openhands.sdk.tool.spec import Tool
from openhands.tools.terminal import TerminalTool


def test_register_client_tools_rejects_builtin_tool_name_collision() -> None:
    spec = ClientToolSpec(
        name=TerminalTool.name,
        description="Client terminal",
    )

    with pytest.raises(
        ClientToolRegistrationError,
        match="collides with an existing non-client tool",
    ):
        register_client_tools([spec])


def test_client_tool_can_share_a_registry_name_across_agents() -> None:
    spec = ClientToolSpec(
        name=TerminalTool.name,
        description="Client terminal",
    )

    tool_specs = register_client_tools([spec], agent_tools=[])

    resolved = resolve_tool(tool_specs[0], MagicMock(spec=ConversationState))
    assert len(resolved) == 1
    assert isinstance(resolved[0], ClientTool)


def test_client_tool_rejects_a_same_agent_name_collision() -> None:
    spec = ClientToolSpec(
        name=TerminalTool.name,
        description="Client terminal",
    )

    with pytest.raises(
        ClientToolRegistrationError,
        match="collides with an existing non-client tool",
    ):
        register_client_tools(
            [spec],
            agent_tools=[
                Tool(
                    name=TerminalTool.name,
                    params={"spec": spec.model_dump()},
                )
            ],
        )


def test_server_tool_spec_params_are_not_resolved_as_a_client_tool() -> None:
    spec = ClientToolSpec(
        name="server_tool_with_spec_params",
        description="Server-owned tool",
    )
    register_client_tools([spec], agent_tools=[])
    server_tool = Tool(name=spec.name, params={"spec": spec.model_dump()})

    assert resolve_client_tool(server_tool, MagicMock(spec=ConversationState)) is None


def test_client_tool_resolution_survives_a_registry_overwrite() -> None:
    spec = ClientToolSpec(
        name="mixed_canvas_tool",
        description="Client canvas tool",
    )
    stored_tools = register_client_tools([spec])
    register_tool(spec.name, TerminalTool)

    restored_tools = register_client_tools([spec], agent_tools=stored_tools)
    resolved = resolve_tool(restored_tools[0], MagicMock(spec=ConversationState))

    assert len(resolved) == 1
    assert isinstance(resolved[0], ClientTool)
