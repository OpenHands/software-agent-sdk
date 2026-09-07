from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from openhands.agent_server.child_conversation_tool import (
    LaunchChildConversationAction,
    LaunchChildConversationExecutor,
    LaunchChildConversationTool,
)
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.tool import Tool
from openhands.sdk.tool.registry import resolve_tool
from openhands.sdk.workspace import LocalWorkspace


def _agent() -> Agent:
    return Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[])


@pytest.mark.parametrize("isolation", ["shared", "worktree"])
def test_action_accepts_supported_isolation(isolation: str) -> None:
    action = LaunchChildConversationAction(
        task="inspect the project", isolation=cast(Any, isolation)
    )
    assert action.isolation == isolation


def test_action_rejects_empty_task_and_unknown_isolation() -> None:
    with pytest.raises(ValidationError):
        LaunchChildConversationAction(task="")
    with pytest.raises(ValidationError):
        LaunchChildConversationAction(task="x", isolation=cast(Any, "remote"))


def test_executor_rejects_unsupported_context() -> None:
    with pytest.raises(RuntimeError, match="not supported"):
        LaunchChildConversationExecutor()(LaunchChildConversationAction(task="x"))


def test_server_tool_resolves_to_executable_definition(tmp_path: Path) -> None:
    agent = _agent()
    from openhands.sdk.conversation.state import ConversationState

    state = ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=tmp_path),
    )
    tool = resolve_tool(Tool(name=LaunchChildConversationTool.name), state)[0]
    assert tool.executor is not None
    assert tool.name == "launch_child_conversation"


@pytest.mark.asyncio
async def test_native_server_launch_exposes_tool_only_on_server_conversation(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent=_agent(),
                workspace=LocalWorkspace(working_dir=workspace_dir),
            )
        )
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        assert "launch_child_conversation" in {
            tool.name for tool in event_service.get_conversation().agent.tools
        }

    standalone = LaunchChildConversationExecutor()
    with pytest.raises(RuntimeError, match="not supported"):
        standalone(LaunchChildConversationAction(task="x"))


@pytest.mark.asyncio
async def test_child_launch_persists_parent_and_shared_workspace(
    tmp_path: Path,
) -> None:
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    async with ConversationService(conversations_dir=conversations_dir) as service:
        parent, _ = await service.start_conversation(
            StartConversationRequest(
                agent=_agent(),
                workspace=LocalWorkspace(working_dir=workspace_dir),
            )
        )
        child = await service._launch_child_conversation(
            parent.id,
            LaunchChildConversationAction(task="inspect files", title="Inspector"),
        )
        child_info = await service.get_conversation(UUID(child.conversation_id))
        assert child_info is not None
        assert child_info.parent_conversation_id == parent.id
        assert child_info.title == "Inspector"
        assert child_info.workspace.working_dir == str(workspace_dir)
        assert child.execution_status in {
            ConversationExecutionStatus.IDLE.value,
            ConversationExecutionStatus.RUNNING.value,
        }


@pytest.mark.asyncio
async def test_child_launch_uses_worktree_isolation(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("test\n")
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo_dir,
        check=True,
    )
    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        conversation_worktree_root=tmp_path / "worktrees",
    ) as service:
        parent, _ = await service.start_conversation(
            StartConversationRequest(
                agent=_agent(), workspace=LocalWorkspace(working_dir=repo_dir)
            )
        )
        child = await service._launch_child_conversation(
            parent.id,
            LaunchChildConversationAction(
                task="work independently", isolation="worktree"
            ),
        )
        child_info = await service.get_conversation(UUID(child.conversation_id))
        assert child_info is not None
        assert child_info.parent_conversation_id == parent.id
        assert child_info.workspace.working_dir != str(repo_dir)
        assert Path(child_info.workspace.working_dir).exists()


def test_legacy_client_tool_spec_for_native_name_is_dropped() -> None:
    """Old clients that still send a ClientToolSpec for launch_child_conversation
    should not cause a collision error; the spec is silently dropped."""
    from openhands.sdk.tool.client_tool import (
        ClientToolSpec,
        register_client_tools,
    )

    specs = [
        ClientToolSpec(
            name="launch_child_conversation",
            description="legacy client-side launcher",
            parameters={"type": "object", "properties": {}},
        )
    ]
    result = register_client_tools(specs)
    assert result == []  # dropped, not registered as a client tool


@pytest.mark.asyncio
async def test_child_inherits_parent_security_policy(tmp_path: Path) -> None:
    """Child conversation should inherit confirmation_policy and security_analyzer
    from the parent."""
    from openhands.sdk.security.confirmation_policy import NeverConfirm

    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    async with ConversationService(conversations_dir=conversations_dir) as service:
        parent, _ = await service.start_conversation(
            StartConversationRequest(
                agent=_agent(),
                workspace=LocalWorkspace(working_dir=workspace_dir),
                confirmation_policy=NeverConfirm(),
            )
        )
        child = await service._launch_child_conversation(
            parent.id,
            LaunchChildConversationAction(task="inspect files"),
        )
        child_info = await service.get_conversation(UUID(child.conversation_id))
        assert child_info is not None
        assert child_info.confirmation_policy == parent.confirmation_policy


@pytest.mark.asyncio
async def test_child_inherits_parent_runtime_controls(tmp_path: Path) -> None:
    """Child conversation must inherit max_iterations, stuck_detection, hooks,
    secrets, and tags from the parent's stored configuration."""
    from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
    from openhands.sdk.secret import StaticSecret

    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    hook_cfg = HookConfig(
        pre_tool_use=[
            HookMatcher(
                matcher="terminal",
                hooks=[HookDefinition(command="echo blocked")],
            )
        ]
    )
    async with ConversationService(conversations_dir=conversations_dir) as service:
        parent, _ = await service.start_conversation(
            StartConversationRequest(
                agent=_agent(),
                workspace=LocalWorkspace(working_dir=workspace_dir),
                max_iterations=42,
                stuck_detection=False,
                hook_config=hook_cfg,
                secrets={"API_KEY": StaticSecret(value="secret-value")},
                tags={"env": "test"},
            )
        )
        child = await service._launch_child_conversation(
            parent.id,
            LaunchChildConversationAction(task="inspect files"),
        )
        child_service = await service.get_event_service(UUID(child.conversation_id))
        assert child_service is not None
        child_stored = child_service.stored
        assert child_stored.max_iterations == 42
        assert child_stored.stuck_detection is False
        assert child_stored.hook_config == hook_cfg
        assert child_stored.tags == {"env": "test"}
