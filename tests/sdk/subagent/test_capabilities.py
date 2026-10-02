"""Configuration ceilings must hold before tools have any side effects."""

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.subagent.capabilities import (
    SubagentCapabilityError,
    SubagentCapabilityLimits,
    prepare_subagent,
)
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool


def _agent(
    tools: list[Tool] | None = None,
    mcp: dict[str, MCPServer] | None = None,
    limits: SubagentCapabilityLimits | None = None,
) -> Agent:
    return Agent(
        llm=TestLLM(model="test-model"),
        tools=tools or [],
        mcp_config=mcp or {},
        subagent_capability_limits=limits,
    )


def test_omitted_limits_preserve_identity_and_serialization() -> None:
    parent = _agent()
    child = _agent([Tool(name="terminal")])
    assert prepare_subagent(parent=parent, child=child, limits=None) is child
    assert "subagent_capability_limits" not in parent.model_dump(mode="json")


@pytest.mark.parametrize("selection", [None, [], [Tool(name="read")]])
def test_none_empty_and_selected_tools_are_distinct(
    selection: list[Tool] | None,
) -> None:
    limits = SubagentCapabilityLimits.from_selection(
        tools=selection, mcp_server_refs=None
    )
    assert limits.tool_names == (
        None if selection is None else tuple(t.name for t in selection)
    )
    assert limits.mcp_server_names is None


def test_empty_limits_reject_tools_and_mcp_independently() -> None:
    tools_only = SubagentCapabilityLimits(tool_names=())
    parent = _agent(limits=tools_only)
    with pytest.raises(SubagentCapabilityError, match="Tool 'terminal'"):
        prepare_subagent(
            parent=parent, child=_agent([Tool(name="terminal")]), limits=tools_only
        )
    mcp_child = _agent(mcp={"server": MCPServer(command="echo")})
    assert prepare_subagent(parent=parent, child=mcp_child, limits=tools_only)
    with pytest.raises(SubagentCapabilityError, match="MCP server"):
        prepare_subagent(
            parent=_agent(),
            child=mcp_child,
            limits=SubagentCapabilityLimits(mcp_server_names=()),
        )


def test_same_tool_name_does_not_authorize_changed_parameters() -> None:
    limits = SubagentCapabilityLimits(tool_names=("read",))
    parent = _agent([Tool(name="read", params={"root": "/safe"})], limits=limits)
    child = _agent([Tool(name="read", params={"root": "/"})])
    with pytest.raises(SubagentCapabilityError, match="parent configuration"):
        prepare_subagent(parent=parent, child=child, limits=limits)


@pytest.mark.parametrize(
    "original,replacement",
    [
        ({"command": "safe"}, {"command": "unsafe"}),
        ({"command": "safe", "args": ["read"]}, {"command": "safe", "args": ["write"]}),
        (
            {"command": "safe", "env": {"TOKEN": "first-secret"}},
            {"command": "safe", "env": {"TOKEN": "second-secret"}},
        ),
        ({"url": "https://safe.test"}, {"url": "https://unsafe.test"}),
        (
            {"url": "https://safe.test", "headers": {"Authorization": "first-secret"}},
            {"url": "https://safe.test", "headers": {"Authorization": "second-secret"}},
        ),
        ({"command": "safe", "enabled": False}, {"command": "safe", "enabled": True}),
    ],
)
def test_same_mcp_name_cannot_change_configuration(
    original: dict, replacement: dict
) -> None:
    limits = SubagentCapabilityLimits(mcp_server_names=("server",))
    parent = _agent(mcp={"server": MCPServer.model_validate(original)}, limits=limits)
    child = _agent(mcp={"server": MCPServer.model_validate(replacement)})
    with pytest.raises(SubagentCapabilityError) as error:
        prepare_subagent(parent=parent, child=child, limits=limits)
    assert "first-secret" not in str(error.value)
    assert "second-secret" not in str(error.value)
    assert "https://" not in str(error.value)


def test_nested_delegation_can_only_narrow_current_capabilities() -> None:
    tools = [Tool(name="read"), Tool(name="search")]
    limits = SubagentCapabilityLimits(
        tool_names=("read", "search"), mcp_server_names=()
    )
    root = _agent(tools, limits=limits)
    child = prepare_subagent(parent=root, child=_agent(tools[:1]), limits=limits)
    grandchild = prepare_subagent(
        parent=child, child=_agent(tools[:1]), limits=child.subagent_capability_limits
    )
    assert grandchild.subagent_capability_limits == child.subagent_capability_limits
    with pytest.raises(SubagentCapabilityError, match="search"):
        prepare_subagent(
            parent=child, child=_agent(tools), limits=child.subagent_capability_limits
        )


def test_child_cannot_add_builtins_or_remove_parent_filter() -> None:
    limits = SubagentCapabilityLimits(tool_names=())
    root = _agent(limits=limits).model_copy(
        update={"include_default_tools": ["FinishTool"]}
    )
    with pytest.raises(SubagentCapabilityError, match="ThinkTool"):
        prepare_subagent(parent=root, child=_agent(), limits=limits)
    root = _agent(limits=limits).model_copy(update={"filter_tools_regex": "^read$"})
    with pytest.raises(SubagentCapabilityError, match="filter"):
        prepare_subagent(parent=root, child=_agent(), limits=limits)


def test_invalid_lazy_tool_is_rejected_before_tool_resolution(tmp_path: Path) -> None:
    limits = SubagentCapabilityLimits(tool_names=())
    child = prepare_subagent(
        parent=_agent(limits=limits), child=_agent(), limits=limits
    )
    changed = child.model_copy(update={"tools": [Tool(name="terminal")]})
    with closing(
        LocalConversation(agent=changed, workspace=tmp_path, visualizer=None)
    ) as conv:
        with patch("openhands.sdk.agent.base.resolve_tool") as resolve:
            with pytest.raises(SubagentCapabilityError):
                conv._ensure_agent_ready()
            resolve.assert_not_called()


def test_sealed_mcp_checks_actual_secrets_after_serialization() -> None:
    limits = SubagentCapabilityLimits(mcp_server_names=("server",))
    mcp = {
        "server": MCPServer(command="safe", env={"TOKEN": SecretStr("first-secret")})
    }
    child = prepare_subagent(
        parent=_agent(mcp=mcp, limits=limits), child=_agent(mcp=mcp), limits=limits
    )
    sealed = child.subagent_capability_limits
    assert sealed is not None
    serialized = sealed.model_dump_json()
    assert "first-secret" not in serialized
    restored = SubagentCapabilityLimits.model_validate_json(serialized)
    restored.check_mcp(mcp)
    with pytest.raises(SubagentCapabilityError, match="configuration changed"):
        restored.check_mcp(
            {
                "server": MCPServer(
                    command="safe", env={"TOKEN": SecretStr("second-secret")}
                )
            }
        )


def test_restore_preserves_ceiling_and_rejects_runtime_override(tmp_path: Path) -> None:
    limits = SubagentCapabilityLimits(tool_names=(), mcp_server_names=())
    child = prepare_subagent(
        parent=_agent(limits=limits), child=_agent(), limits=limits
    )
    with closing(
        LocalConversation(
            agent=child,
            workspace=tmp_path,
            persistence_dir=tmp_path / "state",
            visualizer=None,
        )
    ) as conv:
        conversation_id = conv.id
    with closing(
        LocalConversation(
            agent=None,
            workspace=tmp_path,
            persistence_dir=tmp_path / "state",
            conversation_id=conversation_id,
            visualizer=None,
        )
    ) as restored:
        assert (
            restored.agent.subagent_capability_limits
            == child.subagent_capability_limits
        )
        with pytest.raises(SubagentCapabilityError):
            prepare_subagent(
                parent=restored.agent,
                child=_agent([Tool(name="terminal")]),
                limits=restored.agent.subagent_capability_limits,
            )
    with pytest.raises(SubagentCapabilityError, match="resuming"):
        LocalConversation(
            agent=_agent(),
            workspace=tmp_path,
            persistence_dir=tmp_path / "state",
            conversation_id=conversation_id,
            visualizer=None,
        )


def test_legacy_restore_still_allows_added_tools() -> None:
    persisted = _agent()
    updated = _agent([Tool(name="terminal")])
    assert updated.verify(persisted) is updated


def test_fork_keeps_limits_and_rejects_added_capabilities(tmp_path: Path) -> None:
    limits = SubagentCapabilityLimits(tool_names=(), mcp_server_names=())
    child = prepare_subagent(
        parent=_agent(limits=limits), child=_agent(), limits=limits
    )
    with closing(
        LocalConversation(agent=child, workspace=tmp_path, visualizer=None)
    ) as source:
        with closing(source.fork()) as fork:
            assert (
                fork.agent.subagent_capability_limits
                == child.subagent_capability_limits
            )
            with pytest.raises(SubagentCapabilityError):
                prepare_subagent(
                    parent=fork.agent,
                    child=_agent([Tool(name="terminal")]),
                    limits=fork.agent.subagent_capability_limits,
                )
