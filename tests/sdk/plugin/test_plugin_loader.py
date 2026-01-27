"""Tests for load_plugins() utility and HookConfig.merge()."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.context.skills import Skill
from openhands.sdk.hooks import HookConfig
from openhands.sdk.hooks.config import HookDefinition, HookMatcher
from openhands.sdk.plugin import (
    PluginFetchError,
    PluginSource,
    load_plugins,
)


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


@pytest.fixture
def agent_with_context(mock_llm):
    """Create an agent with existing context."""
    context = AgentContext(
        skills=[Skill(name="existing-skill", content="Existing skill content")]
    )
    return Agent(
        llm=mock_llm,
        tools=[],
        agent_context=context,
    )


@pytest.fixture
def agent_with_mcp(mock_llm):
    """Create an agent with existing MCP config."""
    return Agent(
        llm=mock_llm,
        tools=[],
        mcp_config={"mcpServers": {"existing-server": {"command": "test"}}},
    )


def create_test_plugin(
    plugin_dir: Path,
    name: str = "test-plugin",
    skills: list[dict] | None = None,
    mcp_config: dict | None = None,
    hooks: dict | None = None,
):
    """Helper to create a test plugin directory."""
    # Create plugin structure
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest
    manifest = {"name": name, "version": "1.0.0", "description": f"Test plugin {name}"}
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest))

    # Write skills
    if skills:
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        for skill in skills:
            skill_name = skill["name"]
            skill_content = skill["content"]
            skill_file = skills_dir / f"{skill_name}.md"
            skill_file.write_text(f"---\nname: {skill_name}\n---\n{skill_content}")

    # Write MCP config
    if mcp_config:
        mcp_file = plugin_dir / ".mcp.json"
        mcp_file.write_text(json.dumps(mcp_config))

    # Write hooks
    if hooks:
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(json.dumps(hooks))

    return plugin_dir


class TestHookConfigMerge:
    """Tests for HookConfig.merge class method."""

    def test_merge_empty_list_returns_none(self):
        """Test that empty list returns None."""
        result = HookConfig.merge([])
        assert result is None

    def test_merge_single_config(self):
        """Test merging a single config returns equivalent config."""
        config = HookConfig(
            pre_tool_use=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="test")])
            ]
        )
        result = HookConfig.merge([config])
        assert result is not None
        assert len(result.pre_tool_use) == 1
        assert result.pre_tool_use[0].matcher == "*"

    def test_merge_multiple_pre_tool_use(self):
        """Test merging multiple configs concatenates pre_tool_use."""
        config1 = HookConfig(
            pre_tool_use=[
                HookMatcher(matcher="terminal", hooks=[HookDefinition(command="cmd1")])
            ]
        )
        config2 = HookConfig(
            pre_tool_use=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="cmd2")])
            ]
        )
        result = HookConfig.merge([config1, config2])
        assert result is not None
        assert len(result.pre_tool_use) == 2
        assert result.pre_tool_use[0].matcher == "terminal"
        assert result.pre_tool_use[1].matcher == "*"

    def test_merge_different_event_types(self):
        """Test merging configs with different event types."""
        config1 = HookConfig(
            pre_tool_use=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="pre")])
            ]
        )
        config2 = HookConfig(
            post_tool_use=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="post")])
            ]
        )
        result = HookConfig.merge([config1, config2])
        assert result is not None
        assert len(result.pre_tool_use) == 1
        assert len(result.post_tool_use) == 1

    def test_merge_all_event_types(self):
        """Test merging configs covers all event types."""
        config1 = HookConfig(
            pre_tool_use=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="c1")])
            ],
            session_start=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="c2")])
            ],
        )
        config2 = HookConfig(
            post_tool_use=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="c3")])
            ],
            session_end=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="c4")])
            ],
        )
        config3 = HookConfig(
            user_prompt_submit=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="c5")])
            ],
            stop=[HookMatcher(matcher="*", hooks=[HookDefinition(command="c6")])],
        )
        result = HookConfig.merge([config1, config2, config3])
        assert result is not None
        assert len(result.pre_tool_use) == 1
        assert len(result.post_tool_use) == 1
        assert len(result.user_prompt_submit) == 1
        assert len(result.session_start) == 1
        assert len(result.session_end) == 1
        assert len(result.stop) == 1

    def test_merge_empty_configs_returns_none(self):
        """Test merging only empty configs returns None."""
        config1 = HookConfig()
        config2 = HookConfig()
        result = HookConfig.merge([config1, config2])
        assert result is None


class TestLoadPluginsSinglePlugin:
    """Tests for load_plugins with a single plugin."""

    def test_load_empty_list_returns_unchanged_agent(self, basic_agent):
        """Test that empty plugin list returns agent unchanged."""
        updated_agent, hooks = load_plugins([], basic_agent)
        assert updated_agent is basic_agent
        assert hooks is None

    def test_load_single_plugin_with_skills(self, tmp_path: Path, basic_agent):
        """Test loading a plugin with skills merges into agent context."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            skills=[{"name": "my-skill", "content": "Skill content here"}],
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        updated_agent, hooks = load_plugins(plugins, basic_agent)

        assert updated_agent.agent_context is not None
        assert len(updated_agent.agent_context.skills) == 1
        assert updated_agent.agent_context.skills[0].name == "my-skill"
        assert hooks is None

    def test_load_single_plugin_with_mcp(self, tmp_path: Path, basic_agent):
        """Test loading a plugin with MCP config merges into agent."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            mcp_config={"mcpServers": {"test-server": {"command": "test-cmd"}}},
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        updated_agent, hooks = load_plugins(plugins, basic_agent)

        assert "mcpServers" in updated_agent.mcp_config
        assert "test-server" in updated_agent.mcp_config["mcpServers"]
        assert hooks is None

    def test_load_single_plugin_with_hooks(self, tmp_path: Path, basic_agent):
        """Test loading a plugin with hooks returns hook config."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            hooks={
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "*", "hooks": [{"command": "echo test"}]}
                    ]
                }
            },
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        updated_agent, hooks = load_plugins(plugins, basic_agent)

        assert hooks is not None
        assert len(hooks.pre_tool_use) == 1


class TestLoadPluginsMultiplePlugins:
    """Tests for load_plugins with multiple plugins."""

    def test_load_multiple_plugins_skills_override(self, tmp_path: Path, basic_agent):
        """Test that later plugins override skills by name."""
        plugin1 = create_test_plugin(
            tmp_path / "plugin1",
            name="plugin1",
            skills=[{"name": "shared-skill", "content": "First content"}],
        )
        plugin2 = create_test_plugin(
            tmp_path / "plugin2",
            name="plugin2",
            skills=[{"name": "shared-skill", "content": "Second content"}],
        )

        plugins = [
            PluginSource(source=str(plugin1)),
            PluginSource(source=str(plugin2)),
        ]
        updated_agent, _ = load_plugins(plugins, basic_agent)

        assert updated_agent.agent_context is not None
        assert len(updated_agent.agent_context.skills) == 1
        assert "Second content" in updated_agent.agent_context.skills[0].content

    def test_load_multiple_plugins_mcp_override(self, tmp_path: Path, basic_agent):
        """Test that later plugins override MCP config by key."""
        plugin1 = create_test_plugin(
            tmp_path / "plugin1",
            name="plugin1",
            mcp_config={"mcpServers": {"server": {"command": "first"}}},
        )
        plugin2 = create_test_plugin(
            tmp_path / "plugin2",
            name="plugin2",
            mcp_config={"mcpServers": {"server": {"command": "second"}}},
        )

        plugins = [
            PluginSource(source=str(plugin1)),
            PluginSource(source=str(plugin2)),
        ]
        updated_agent, _ = load_plugins(plugins, basic_agent)

        assert updated_agent.mcp_config["mcpServers"]["server"]["command"] == "second"

    def test_load_multiple_plugins_hooks_concatenate(self, tmp_path: Path, basic_agent):
        """Test that hooks from all plugins are concatenated."""
        plugin1 = create_test_plugin(
            tmp_path / "plugin1",
            name="plugin1",
            hooks={
                "hooks": {
                    "PreToolUse": [{"matcher": "a", "hooks": [{"command": "c1"}]}]
                }
            },
        )
        plugin2 = create_test_plugin(
            tmp_path / "plugin2",
            name="plugin2",
            hooks={
                "hooks": {
                    "PreToolUse": [{"matcher": "b", "hooks": [{"command": "c2"}]}]
                }
            },
        )

        plugins = [
            PluginSource(source=str(plugin1)),
            PluginSource(source=str(plugin2)),
        ]
        _, hooks = load_plugins(plugins, basic_agent)

        assert hooks is not None
        assert len(hooks.pre_tool_use) == 2

    def test_load_multiple_plugins_different_skills(self, tmp_path: Path, basic_agent):
        """Test that different skills from multiple plugins are combined."""
        plugin1 = create_test_plugin(
            tmp_path / "plugin1",
            name="plugin1",
            skills=[{"name": "skill-a", "content": "A"}],
        )
        plugin2 = create_test_plugin(
            tmp_path / "plugin2",
            name="plugin2",
            skills=[{"name": "skill-b", "content": "B"}],
        )

        plugins = [
            PluginSource(source=str(plugin1)),
            PluginSource(source=str(plugin2)),
        ]
        updated_agent, _ = load_plugins(plugins, basic_agent)

        assert updated_agent.agent_context is not None
        skill_names = [s.name for s in updated_agent.agent_context.skills]
        assert "skill-a" in skill_names
        assert "skill-b" in skill_names


class TestLoadPluginsWithExistingContext:
    """Tests for load_plugins with agents that have existing context."""

    def test_preserves_existing_skills(self, tmp_path: Path, agent_with_context):
        """Test that existing skills are preserved when loading plugins."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            skills=[{"name": "new-skill", "content": "New content"}],
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        updated_agent, _ = load_plugins(plugins, agent_with_context)

        assert updated_agent.agent_context is not None
        skill_names = [s.name for s in updated_agent.agent_context.skills]
        assert "existing-skill" in skill_names
        assert "new-skill" in skill_names

    def test_plugin_skill_overrides_existing(self, tmp_path: Path, agent_with_context):
        """Test that plugin skill with same name overrides existing."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            skills=[{"name": "existing-skill", "content": "Plugin content"}],
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        updated_agent, _ = load_plugins(plugins, agent_with_context)

        assert updated_agent.agent_context is not None
        assert len(updated_agent.agent_context.skills) == 1
        assert "Plugin content" in updated_agent.agent_context.skills[0].content

    def test_preserves_existing_mcp(self, tmp_path: Path, agent_with_mcp):
        """Test that existing MCP config is preserved."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            mcp_config={"mcpServers": {"new-server": {"command": "new"}}},
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        updated_agent, _ = load_plugins(plugins, agent_with_mcp)

        assert "existing-server" in updated_agent.mcp_config["mcpServers"]
        assert "new-server" in updated_agent.mcp_config["mcpServers"]


class TestLoadPluginsMaxSkills:
    """Tests for max_skills limit enforcement."""

    def test_max_skills_not_exceeded(self, tmp_path: Path, basic_agent):
        """Test that loading succeeds when under max_skills."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            skills=[
                {"name": "skill-1", "content": "C1"},
                {"name": "skill-2", "content": "C2"},
            ],
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        updated_agent, _ = load_plugins(plugins, basic_agent, max_skills=10)

        assert updated_agent.agent_context is not None
        assert len(updated_agent.agent_context.skills) == 2

    def test_max_skills_exceeded_raises_error(self, tmp_path: Path, basic_agent):
        """Test that exceeding max_skills raises ValueError."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            skills=[
                {"name": "skill-1", "content": "C1"},
                {"name": "skill-2", "content": "C2"},
                {"name": "skill-3", "content": "C3"},
            ],
        )

        plugins = [PluginSource(source=str(plugin_dir))]
        with pytest.raises(ValueError, match="exceeds maximum"):
            load_plugins(plugins, basic_agent, max_skills=2)


class TestLoadPluginsErrorHandling:
    """Tests for error handling in load_plugins."""

    def test_nonexistent_plugin_raises_error(self, basic_agent):
        """Test that nonexistent plugin path raises error."""
        plugins = [PluginSource(source="/nonexistent/path")]
        with pytest.raises(PluginFetchError):
            load_plugins(plugins, basic_agent)

    def test_invalid_plugin_dir_raises_error(self, tmp_path: Path, basic_agent):
        """Test that invalid plugin (no manifest) still loads with inferred manifest."""
        # Create an empty directory (no manifest)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        plugins = [PluginSource(source=str(empty_dir))]
        # Should not raise - Plugin.load() infers manifest from directory name
        updated_agent, _ = load_plugins(plugins, basic_agent)
        assert updated_agent is not None


class TestPluginSource:
    """Tests for PluginSource model."""

    def test_create_basic(self):
        """Test creating a basic PluginSource."""
        source = PluginSource(source="github:owner/repo")
        assert source.source == "github:owner/repo"
        assert source.ref is None
        assert source.repo_path is None

    def test_create_with_ref(self):
        """Test creating PluginSource with ref."""
        source = PluginSource(source="github:owner/repo", ref="v1.0.0")
        assert source.ref == "v1.0.0"

    def test_create_with_repo_path(self):
        """Test creating PluginSource with repo_path."""
        source = PluginSource(
            source="github:owner/monorepo",
            repo_path="plugins/my-plugin",
        )
        assert source.repo_path == "plugins/my-plugin"

    def test_create_local_path(self):
        """Test creating PluginSource with local path."""
        source = PluginSource(source="/path/to/plugin")
        assert source.source == "/path/to/plugin"
