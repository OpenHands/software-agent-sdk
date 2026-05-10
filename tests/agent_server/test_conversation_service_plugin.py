"""Tests for plugin handling in ConversationService.

This module tests plugin handling via the `plugins` list parameter
on StartConversationRequest.

These tests verify that:
1. Plugin specs are passed through to StoredConversation (for lazy loading)
2. Explicit hook_config is preserved (merging happens lazily in LocalConversation)
3. Plugins ARE persisted (unlike the old eager loading model) since
   LocalConversation loads them lazily on first run()/send_message()
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
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher, HookType
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
async def test_start_conversation_without_plugin(conversation_service):
    """Test start_conversation works without plugin configuration."""
    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4o", usage_id="test-llm"),
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


# Tests for plugins list parameter


@pytest.mark.asyncio
async def test_start_conversation_with_plugins_list(conversation_service, tmp_path):
    """Test start_conversation passes plugins to StoredConversation for lazy loading."""
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
                llm=LLM(model="gpt-4o", usage_id="test-llm"),
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

            # Verify plugins are passed through for lazy loading
            stored = mock_event_service_class.call_args.kwargs["stored"]
            # Plugins should be stored (not loaded yet - lazy loading)
            assert stored.plugins is not None
            assert len(stored.plugins) == 1
            assert stored.plugins[0].source == str(plugin_dir)
            # Agent context NOT populated yet (lazy loading in LocalConversation)
            assert stored.agent.agent_context is None


@pytest.mark.asyncio
async def test_start_conversation_with_multiple_plugins(conversation_service, tmp_path):
    """Test start_conversation with multiple plugins stored for lazy loading."""
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
                llm=LLM(model="gpt-4o", usage_id="test-llm"),
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

            # Verify both plugins are stored for lazy loading
            stored = mock_event_service_class.call_args.kwargs["stored"]
            assert stored.plugins is not None
            assert len(stored.plugins) == 2
            plugin_sources = [p.source for p in stored.plugins]
            assert str(plugin1_dir) in plugin_sources
            assert str(plugin2_dir) in plugin_sources


@pytest.mark.asyncio
async def test_plugins_persisted_in_stored_conversation_for_lazy_loading(
    conversation_service, tmp_path
):
    """Test that plugins ARE persisted for lazy loading by LocalConversation."""
    plugin_dir = create_test_plugin_dir(
        tmp_path,
        skills=[{"name": "test-skill", "content": "Test"}],
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4o", usage_id="test-llm"),
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

            # Verify plugins ARE persisted (for lazy loading)
            # LocalConversation will load them on first run()/send_message()
            stored = mock_event_service_class.call_args.kwargs["stored"]
            assert stored.plugins is not None
            assert len(stored.plugins) == 1
            assert stored.plugins[0].source == str(plugin_dir)


# Tests for explicit hook_config


def test_start_conversation_request_has_hook_config_field():
    """Verify StartConversationRequest has hook_config field."""
    fields = StartConversationRequest.model_fields
    assert "hook_config" in fields


@pytest.mark.asyncio
async def test_start_conversation_with_explicit_hook_config(conversation_service):
    """Test start_conversation with explicit hook_config (no plugins)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        explicit_hooks = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="*",
                    hooks=[
                        HookDefinition(type=HookType.COMMAND, command="echo explicit")
                    ],
                )
            ]
        )
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4o", usage_id="test-llm"),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
            hook_config=explicit_hooks,
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

            # Verify explicit hook_config is used
            stored = mock_event_service_class.call_args.kwargs["stored"]
            assert stored.hook_config is not None
            assert len(stored.hook_config.pre_tool_use) == 1
            hook_cmd = stored.hook_config.pre_tool_use[0].hooks[0].command
            assert hook_cmd == "echo explicit"


@pytest.mark.asyncio
async def test_start_conversation_stores_both_hooks_and_plugins_for_lazy_merge(
    conversation_service, tmp_path
):
    """Test that explicit hook_config and plugins are both stored (merging is lazy)."""
    # Create plugin with hooks
    plugin_dir = create_test_plugin_dir(
        tmp_path,
        hooks={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "echo plugin"}],
                    }
                ]
            }
        },
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        explicit_hooks = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="*",
                    hooks=[
                        HookDefinition(type=HookType.COMMAND, command="echo explicit")
                    ],
                )
            ]
        )
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4o", usage_id="test-llm"),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir=temp_dir),
            plugins=[PluginSource(source=str(plugin_dir))],
            hook_config=explicit_hooks,
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

            # Verify both explicit hooks AND plugins are stored
            # (merging happens lazily in LocalConversation._ensure_plugins_loaded)
            stored = mock_event_service_class.call_args.kwargs["stored"]

            # Explicit hook_config is stored as-is (not merged yet)
            assert stored.hook_config is not None
            assert len(stored.hook_config.pre_tool_use) == 1
            assert (
                stored.hook_config.pre_tool_use[0].hooks[0].command == "echo explicit"
            )

            # Plugins are stored for lazy loading
            assert stored.plugins is not None
            assert len(stored.plugins) == 1
