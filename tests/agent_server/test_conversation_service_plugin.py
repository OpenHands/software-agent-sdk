"""Tests for plugin loading via AgentContext in ConversationService.

Plugin loading has moved from StartConversationRequest to AgentContext.
These tests verify that:
1. Plugins are loaded when agent.agent_context.plugin_source is set
2. Hooks are extracted from agent_context.plugin_hooks in start_conversation()
3. The old plugin_source fields are no longer on StartConversationRequest
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


def test_start_conversation_request_has_no_plugin_fields():
    """Verify plugin_source, plugin_ref, plugin_path are removed from request model."""
    # These fields should not exist on StartConversationRequest anymore
    fields = StartConversationRequest.model_fields
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
