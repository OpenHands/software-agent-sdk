"""Tests for plugin loading in ConversationService.

This module tests two approaches to plugin loading:
1. New approach: Using `plugins` list parameter on StartConversationRequest
2. Legacy approach: Using agent.agent_context.plugin_source (deprecated)

These tests verify that:
1. Plugins are loaded from the `plugins` list using load_plugins()
2. Legacy AgentContext plugin loading still works for backward compatibility
3. Hooks, skills, and MCP config are properly merged
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    StartConversationRequest,
    StoredConversation,
)
from openhands.sdk import LLM
from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.plugin import PluginSource
from openhands.sdk.workspace import LocalWorkspace


def create_test_plugin_dir(
    tmp_path: Path,
    *,
    skills: list[dict] | None = None,
    hooks: dict | None = None,
    mcp_config: dict | None = None,
) -> Path:
    """Create a test plugin directory structure."""
    import json

    plugin_dir = tmp_path / "test-plugin"
    plugin_dir.mkdir(parents=True)

    # Create manifest
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir()
    manifest_file = manifest_dir / "plugin.json"
    manifest_file.write_text('{"name": "test-plugin", "version": "1.0.0"}')

    # Create skills
    if skills:
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        for skill_data in skills:
            skill_dir = skills_dir / skill_data["name"]
            skill_dir.mkdir()
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                f"""---
name: {skill_data["name"]}
description: Test skill
---

{skill_data.get("content", "Test content")}
"""
            )

    # Create hooks
    if hooks:
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()
        hooks_json = hooks_dir / "hooks.json"
        hooks_json.write_text(json.dumps(hooks))

    # Create MCP config
    if mcp_config:
        mcp_json = plugin_dir / ".mcp.json"
        mcp_json.write_text(json.dumps(mcp_config))

    return plugin_dir


@pytest.fixture
def conversation_service():
    """Create a ConversationService instance for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        service = ConversationService(
            conversations_dir=Path(temp_dir) / "conversations",
        )
        service._event_services = {}
        yield service


def test_start_conversation_request_has_plugins_field():
    """Verify StartConversationRequest has plugins list field (not legacy fields)."""
    fields = StartConversationRequest.model_fields
    # New plugins list field should exist
    assert "plugins" in fields
    # Legacy individual plugin fields should not exist
    assert "plugin_source" not in fields
    assert "plugin_ref" not in fields
    assert "plugin_path" not in fields


@pytest.mark.asyncio
async def test_start_conversation_extracts_hooks_from_agent_context(
    conversation_service, tmp_path
):
    """Test that hooks are extracted from agent_context.plugin_hooks."""
    # Create plugin with hooks
    plugin_dir = create_test_plugin_dir(
        tmp_path,
        hooks={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "echo test"}],
                    }
                ]
            }
        },
    )

    # Create AgentContext with plugin_source
    agent_context = AgentContext(plugin_source=str(plugin_dir))

    # Verify hooks were loaded
    assert agent_context.plugin_hooks is not None
    assert len(agent_context.plugin_hooks.pre_tool_use) == 1

    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
                agent_context=agent_context,
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
        )

        with patch(
            "openhands.agent_server.conversation_service.EventService"
        ) as mock_event_service_class:
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service_class.return_value = mock_event_service

            mock_state = ConversationState(
                id=uuid4(),
                agent=request.agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=request.confirmation_policy,
            )
            mock_event_service.get_state.return_value = mock_state
            mock_event_service.stored = StoredConversation(
                id=mock_state.id,
                **request.model_dump(),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            await conversation_service.start_conversation(request)

            # Verify hooks were extracted and stored in StoredConversation
            stored = mock_event_service_class.call_args.kwargs["stored"]
            assert stored.hook_config is not None
            assert len(stored.hook_config.pre_tool_use) == 1


@pytest.mark.asyncio
async def test_start_conversation_without_plugin(conversation_service):
    """Test start_conversation works without plugin configuration."""
    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
        )

        with patch(
            "openhands.agent_server.conversation_service.EventService"
        ) as mock_event_service_class:
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service_class.return_value = mock_event_service

            mock_state = ConversationState(
                id=uuid4(),
                agent=request.agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=request.confirmation_policy,
            )
            mock_event_service.get_state.return_value = mock_state
            mock_event_service.stored = StoredConversation(
                id=mock_state.id,
                **request.model_dump(),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            await conversation_service.start_conversation(request)

            # Verify hook_config is None when no plugin
            stored = mock_event_service_class.call_args.kwargs["stored"]
            assert stored.hook_config is None


@pytest.mark.asyncio
async def test_start_conversation_with_plugin_skills(conversation_service, tmp_path):
    """Test that plugin skills are merged into agent_context."""
    # Create plugin with skills
    plugin_dir = create_test_plugin_dir(
        tmp_path,
        skills=[{"name": "plugin-skill", "content": "Plugin skill content"}],
    )

    # Create AgentContext with plugin_source
    agent_context = AgentContext(plugin_source=str(plugin_dir))

    # Verify skill was loaded into AgentContext
    assert len(agent_context.skills) == 1
    assert agent_context.skills[0].name == "plugin-skill"

    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
                agent_context=agent_context,
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
        )

        with patch(
            "openhands.agent_server.conversation_service.EventService"
        ) as mock_event_service_class:
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service_class.return_value = mock_event_service

            mock_state = ConversationState(
                id=uuid4(),
                agent=request.agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=request.confirmation_policy,
            )
            mock_event_service.get_state.return_value = mock_state
            mock_event_service.stored = StoredConversation(
                id=mock_state.id,
                **request.model_dump(),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            await conversation_service.start_conversation(request)

            # Verify skills are in the stored agent_context
            stored = mock_event_service_class.call_args.kwargs["stored"]
            assert len(stored.agent.agent_context.skills) == 1
            assert stored.agent.agent_context.skills[0].name == "plugin-skill"


# New tests for plugins list parameter


@pytest.mark.asyncio
async def test_start_conversation_with_plugins_list(conversation_service, tmp_path):
    """Test start_conversation with plugins list parameter."""
    # Create plugin with hooks and skills
    plugin_dir = create_test_plugin_dir(
        tmp_path,
        skills=[{"name": "test-skill", "content": "Test skill content"}],
        hooks={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "echo pre"}],
                    }
                ]
            }
        },
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
            plugins=[PluginSource(source=str(plugin_dir))],
        )

        with patch(
            "openhands.agent_server.conversation_service.EventService"
        ) as mock_event_service_class:
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service_class.return_value = mock_event_service

            mock_state = ConversationState(
                id=uuid4(),
                agent=request.agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=request.confirmation_policy,
            )
            mock_event_service.get_state.return_value = mock_state
            mock_event_service.stored = StoredConversation(
                id=mock_state.id,
                agent=request.agent,
                **request.model_dump(exclude={"agent"}),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            await conversation_service.start_conversation(request)

            # Verify plugin was loaded
            stored = mock_event_service_class.call_args.kwargs["stored"]
            # Skills should be merged
            assert len(stored.agent.agent_context.skills) == 1
            assert stored.agent.agent_context.skills[0].name == "test-skill"
            # Hooks should be extracted
            assert stored.hook_config is not None
            assert len(stored.hook_config.pre_tool_use) == 1


@pytest.mark.asyncio
async def test_start_conversation_with_multiple_plugins(conversation_service, tmp_path):
    """Test start_conversation with multiple plugins."""
    # Create two plugins
    plugin1_dir = create_test_plugin_dir(
        tmp_path / "plugin1",
        skills=[{"name": "skill-a", "content": "Skill A"}],
    )
    plugin2_dir = create_test_plugin_dir(
        tmp_path / "plugin2",
        skills=[{"name": "skill-b", "content": "Skill B"}],
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
            plugins=[
                PluginSource(source=str(plugin1_dir)),
                PluginSource(source=str(plugin2_dir)),
            ],
        )

        with patch(
            "openhands.agent_server.conversation_service.EventService"
        ) as mock_event_service_class:
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service_class.return_value = mock_event_service

            mock_state = ConversationState(
                id=uuid4(),
                agent=request.agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=request.confirmation_policy,
            )
            mock_event_service.get_state.return_value = mock_state
            mock_event_service.stored = StoredConversation(
                id=mock_state.id,
                agent=request.agent,
                **request.model_dump(exclude={"agent"}),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            await conversation_service.start_conversation(request)

            # Verify both plugins were loaded
            stored = mock_event_service_class.call_args.kwargs["stored"]
            skill_names = [s.name for s in stored.agent.agent_context.skills]
            assert "skill-a" in skill_names
            assert "skill-b" in skill_names


@pytest.mark.asyncio
async def test_plugins_not_persisted_in_stored_conversation(
    conversation_service, tmp_path
):
    """Test that plugins list is not persisted (only loaded content is)."""
    plugin_dir = create_test_plugin_dir(
        tmp_path,
        skills=[{"name": "test-skill", "content": "Test"}],
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
            plugins=[PluginSource(source=str(plugin_dir))],
        )

        with patch(
            "openhands.agent_server.conversation_service.EventService"
        ) as mock_event_service_class:
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service_class.return_value = mock_event_service

            mock_state = ConversationState(
                id=uuid4(),
                agent=request.agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=request.confirmation_policy,
            )
            mock_event_service.get_state.return_value = mock_state
            mock_event_service.stored = StoredConversation(
                id=mock_state.id,
                agent=request.agent,
                **request.model_dump(exclude={"agent"}),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            await conversation_service.start_conversation(request)

            # Verify plugins list is not in stored data
            # (it's excluded since content is already loaded)
            stored = mock_event_service_class.call_args.kwargs["stored"]
            # The stored object should not have plugins field set
            # (since it was excluded in model_dump)
            assert stored.plugins is None
