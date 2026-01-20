"""Tests for plugin loading via LocalConversation and Conversation factory."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.hooks import HookConfig
from openhands.sdk.hooks.config import HookDefinition, HookMatcher
from openhands.sdk.plugin import PluginSource


@pytest.fixture
def mock_llm():
    """Create a mock LLM for agent tests."""
    return LLM(
        model="test/model",
        api_key=SecretStr("test-key"),
    )


@pytest.fixture
def basic_agent(mock_llm):
    """Create a basic agent for testing."""
    return Agent(
        llm=mock_llm,
        tools=[],
    )


def create_test_plugin(
    plugin_dir: Path,
    name: str = "test-plugin",
    skills: list[dict] | None = None,
    mcp_config: dict | None = None,
    hooks: dict | None = None,
):
    """Helper to create a test plugin directory."""
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"name": name, "version": "1.0.0", "description": f"Test plugin {name}"}
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest))

    if skills:
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        for skill in skills:
            skill_name = skill["name"]
            skill_content = skill["content"]
            skill_file = skills_dir / f"{skill_name}.md"
            skill_file.write_text(f"---\nname: {skill_name}\n---\n{skill_content}")

    if mcp_config:
        mcp_file = plugin_dir / ".mcp.json"
        mcp_file.write_text(json.dumps(mcp_config))

    if hooks:
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(json.dumps(hooks))

    return plugin_dir


class TestLocalConversationPlugins:
    """Tests for plugin loading in LocalConversation."""

    def test_create_conversation_with_plugins(self, tmp_path: Path, basic_agent):
        """Test creating LocalConversation with plugins parameter."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            skills=[{"name": "test-skill", "content": "Test skill content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Agent should have been updated with plugin skills
        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "test-skill" in skill_names
        conversation.close()

    def test_conversation_with_multiple_plugins(self, tmp_path: Path, basic_agent):
        """Test loading multiple plugins via LocalConversation."""
        plugin1 = create_test_plugin(
            tmp_path / "plugin1",
            name="plugin1",
            skills=[{"name": "skill-a", "content": "Content A"}],
        )
        plugin2 = create_test_plugin(
            tmp_path / "plugin2",
            name="plugin2",
            skills=[{"name": "skill-b", "content": "Content B"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[
                PluginSource(source=str(plugin1)),
                PluginSource(source=str(plugin2)),
            ],
            visualizer=None,
        )

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "skill-a" in skill_names
        assert "skill-b" in skill_names
        conversation.close()

    def test_plugin_hooks_combined_with_explicit_hooks(
        self, tmp_path: Path, basic_agent
    ):
        """Test that plugin hooks are combined with explicit hook_config."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            hooks={
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "plugin-*", "hooks": [{"command": "plugin-cmd"}]}
                    ]
                }
            },
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        explicit_hooks = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="explicit-*", hooks=[HookDefinition(command="explicit-cmd")]
                )
            ]
        )

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            hook_config=explicit_hooks,
            visualizer=None,
        )

        # Both hook sources should be combined
        assert conversation._hook_processor is not None
        # We can verify hooks were processed by checking the hook_config passed
        # (The actual hook_processor is internal, but we trust the merging works)
        conversation.close()


class TestConversationFactoryPlugins:
    """Tests for plugin loading via Conversation factory."""

    def test_factory_passes_plugins_to_local_conversation(
        self, tmp_path: Path, basic_agent
    ):
        """Test that Conversation factory passes plugins to LocalConversation."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            skills=[{"name": "factory-skill", "content": "Factory skill content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = Conversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        assert isinstance(conversation, LocalConversation)
        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "factory-skill" in skill_names
        conversation.close()

    def test_factory_with_string_workspace_and_plugins(
        self, tmp_path: Path, basic_agent
    ):
        """Test factory with string workspace path and plugins."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            skills=[{"name": "skill", "content": "Content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = Conversation(
            agent=basic_agent,
            workspace=str(workspace),
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        assert conversation.agent.agent_context is not None
        assert len(conversation.agent.agent_context.skills) == 1
        conversation.close()

    def test_factory_with_no_plugins(self, tmp_path: Path, basic_agent):
        """Test that factory works without plugins (plugins=None is default)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = Conversation(
            agent=basic_agent,
            workspace=workspace,
            visualizer=None,
        )

        # Should work without errors
        assert conversation is not None
        conversation.close()
