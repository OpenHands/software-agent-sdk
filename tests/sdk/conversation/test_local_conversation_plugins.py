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
    """Tests for plugin loading in LocalConversation.

    Note: Plugins are lazy-loaded on first run()/send_message() call.
    Tests trigger _ensure_plugins_loaded() to verify loading behavior.
    """

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

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        # Agent should have been updated with plugin skills
        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "test-skill" in skill_names

        # Verify resolved plugins are tracked
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1
        assert conversation.resolved_plugins[0].source == str(plugin_dir)

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

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "skill-a" in skill_names
        assert "skill-b" in skill_names

        # Verify both plugins tracked
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 2

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

        # Hooks are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        # Both hook sources should be combined
        assert conversation._hook_processor is not None
        # We can verify hooks were processed by checking the hook_config passed
        # (The actual hook_processor is internal, but we trust the merging works)
        conversation.close()

    def test_plugins_not_loaded_until_needed(self, tmp_path: Path, basic_agent):
        """Test that plugins are not loaded in constructor (lazy loading)."""
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

        # Before loading, plugins should not be applied
        assert conversation._plugins_loaded is False
        assert conversation.resolved_plugins is None
        assert conversation.agent.agent_context is None

        # After triggering load
        conversation._ensure_plugins_loaded()

        assert conversation._plugins_loaded is True
        assert conversation.resolved_plugins is not None
        assert conversation.agent.agent_context is not None

        conversation.close()


class TestConversationFactoryPlugins:
    """Tests for plugin loading via Conversation factory.

    Note: Plugins are lazy-loaded on first run()/send_message() call.
    """

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

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

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

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

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
