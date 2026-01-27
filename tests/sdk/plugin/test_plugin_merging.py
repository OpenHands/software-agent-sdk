"""Tests for plugin merging utilities."""

import pytest

from openhands.sdk.context import AgentContext
from openhands.sdk.context.skills import Skill
from openhands.sdk.plugin import Plugin, PluginManifest


class TestPluginAddSkillsTo:
    """Tests for Plugin.add_skills_to() method."""

    def test_add_skills_to_empty_plugin(self, empty_plugin):
        """Test adding skills from empty plugin returns unchanged context."""
        context = AgentContext(skills=[])
        new_context = empty_plugin.add_skills_to(context)
        assert new_context.skills == []

    def test_add_skills_to_none_context_empty_plugin(self, empty_plugin):
        """Test adding skills with None context and empty plugin."""
        new_context = empty_plugin.add_skills_to(None)
        assert isinstance(new_context, AgentContext)
        assert new_context.skills == []

    def test_add_skills_to_none_input(self, mock_plugin_with_skills):
        """Test adding skills with None input creates new context."""
        new_context = mock_plugin_with_skills.add_skills_to()
        assert isinstance(new_context, AgentContext)
        assert len(new_context.skills) > 0

    def test_add_skills_to_with_skills(self, mock_plugin_with_skills):
        """Test adding plugin skills to context."""
        context = AgentContext(skills=[])
        new_context = mock_plugin_with_skills.add_skills_to(context)
        assert len(new_context.skills) == len(mock_plugin_with_skills.skills)

    def test_add_skills_to_adds_new_skill(self, mock_skill, another_mock_skill):
        """Test adding skills adds new skill when no conflict."""
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[another_mock_skill],
        )
        context = AgentContext(skills=[mock_skill])
        new_context = plugin.add_skills_to(context)
        assert len(new_context.skills) == 2
        skill_names = {s.name for s in new_context.skills}
        assert skill_names == {mock_skill.name, another_mock_skill.name}

    def test_add_skills_to_overrides_existing_skill(self):
        """Test plugin skill overrides existing skill with same name."""
        original_skill = Skill(name="test-skill", content="Original content")
        updated_skill = Skill(name="test-skill", content="Updated content")
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[updated_skill],
        )
        context = AgentContext(skills=[original_skill])
        new_context = plugin.add_skills_to(context)
        assert len(new_context.skills) == 1
        assert new_context.skills[0].content == "Updated content"

    def test_add_skills_to_preserves_insertion_order(self):
        """Test add_skills_to preserves order of existing skills."""
        skill_a = Skill(name="skill-a", content="A")
        skill_b = Skill(name="skill-b", content="B")
        skill_c = Skill(name="skill-c", content="C")
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[skill_c],
        )
        context = AgentContext(skills=[skill_a, skill_b])
        new_context = plugin.add_skills_to(context)
        skill_names = [s.name for s in new_context.skills]
        assert skill_names == ["skill-a", "skill-b", "skill-c"]

    def test_add_skills_to_returns_new_context(self, mock_skill):
        """Test add_skills_to returns new context instance, not modifying original."""
        new_skill = Skill(name="new-skill", content="New")
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[new_skill],
        )
        original_context = AgentContext(skills=[mock_skill])
        new_context = plugin.add_skills_to(original_context)
        # Original context should be unchanged
        assert len(original_context.skills) == 1
        assert len(new_context.skills) == 2
        assert new_context is not original_context

    def test_add_skills_to_enforces_max_skills(self, mock_plugin_with_skills):
        """Test add_skills_to enforces max_skills limit."""
        context = AgentContext(skills=[])
        with pytest.raises(ValueError, match="exceeds maximum"):
            mock_plugin_with_skills.add_skills_to(context, max_skills=0)

    def test_add_skills_to_max_skills_with_existing(self, mock_skill):
        """Test max_skills counts unique skills after merge."""
        plugin_skill_1 = Skill(name="plugin-skill-1", content="P1")
        plugin_skill_2 = Skill(name="plugin-skill-2", content="P2")
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[plugin_skill_1, plugin_skill_2],
        )
        context = AgentContext(skills=[mock_skill])

        # Limit of 3 should allow merge (1 existing + 2 new = 3)
        new_context = plugin.add_skills_to(context, max_skills=3)
        assert len(new_context.skills) == 3

        # Limit of 2 should fail (3 > 2)
        with pytest.raises(ValueError, match="exceeds maximum"):
            plugin.add_skills_to(context, max_skills=2)

    def test_add_skills_to_max_skills_with_override(self):
        """Test max_skills counts correctly when plugin overrides existing skill."""
        existing_skill = Skill(name="shared-skill", content="Old")
        context = AgentContext(skills=[existing_skill])

        plugin_skill = Skill(name="shared-skill", content="New")
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[plugin_skill],
        )

        new_context = plugin.add_skills_to(context, max_skills=1)
        assert len(new_context.skills) == 1
        assert new_context.skills[0].content == "New"

    def test_add_skills_to_preserves_context_fields(self, mock_plugin_with_skills):
        """Test add_skills_to preserves other AgentContext fields."""
        context = AgentContext(
            skills=[],
            system_message_suffix="Custom suffix",
        )
        new_context = mock_plugin_with_skills.add_skills_to(context)
        assert new_context.system_message_suffix == context.system_message_suffix


class TestPluginAddMcpConfigTo:
    """Tests for Plugin.add_mcp_config_to() method."""

    def test_add_mcp_config_to_empty_plugin(self, empty_plugin):
        """Test adding MCP config from empty plugin returns empty dict."""
        new_mcp = empty_plugin.add_mcp_config_to({})
        assert new_mcp == {}

    def test_add_mcp_config_to_both_none(self, empty_plugin):
        """Test adding MCP config with both None returns empty dict."""
        new_mcp = empty_plugin.add_mcp_config_to(None)
        assert new_mcp == {}

    def test_add_mcp_config_to_none_input(self, mock_plugin_with_mcp):
        """Test adding MCP config with None input."""
        new_mcp = mock_plugin_with_mcp.add_mcp_config_to()
        assert isinstance(new_mcp, dict)
        assert new_mcp == mock_plugin_with_mcp.mcp_config

    def test_add_mcp_config_to_with_config(self, mock_plugin_with_mcp):
        """Test adding plugin MCP config."""
        new_mcp = mock_plugin_with_mcp.add_mcp_config_to({})
        assert new_mcp == mock_plugin_with_mcp.mcp_config

    def test_add_mcp_config_to_both_empty(self):
        """Test adding MCP config with both empty returns empty dict."""
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            mcp_config={},
        )
        new_mcp = plugin.add_mcp_config_to({})
        assert new_mcp == {}

    def test_add_mcp_config_to_merges_configs(self):
        """Test add_mcp_config_to returns correctly merged MCP config."""
        base_mcp = {"server1": {"command": "base"}}
        plugin_mcp = {"server2": {"command": "plugin"}}

        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            mcp_config=plugin_mcp,
        )

        new_mcp = plugin.add_mcp_config_to(base_mcp)

        assert "server1" in new_mcp
        assert "server2" in new_mcp
        assert new_mcp["server1"]["command"] == "base"
        assert new_mcp["server2"]["command"] == "plugin"

    def test_add_mcp_config_to_plugin_overrides(self):
        """Test plugin config overrides base config for same key."""
        base_mcp = {"server1": {"command": "python", "args": ["-m", "base_server"]}}
        plugin_mcp = {"server1": {"command": "python", "args": ["-m", "plugin_server"]}}

        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            mcp_config=plugin_mcp,
        )

        new_mcp = plugin.add_mcp_config_to(base_mcp)
        assert new_mcp["server1"]["args"] == ["-m", "plugin_server"]

    def test_add_mcp_config_to_does_not_modify_inputs(self):
        """Test add_mcp_config_to does not modify input dicts."""
        base_mcp = {"server1": {"command": "python"}}
        plugin_mcp = {"server2": {"command": "node"}}
        original_base = base_mcp.copy()
        original_plugin = plugin_mcp.copy()

        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            mcp_config=plugin_mcp,
        )

        plugin.add_mcp_config_to(base_mcp)

        assert base_mcp == original_base
        assert plugin_mcp == original_plugin

    def test_add_mcp_config_to_merges_mcp_servers(self):
        """Test add_mcp_config_to merges mcpServers by server name."""
        base_mcp = {"mcpServers": {"server1": {"command": "base"}}}
        plugin_mcp = {"mcpServers": {"server2": {"command": "plugin"}}}

        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            mcp_config=plugin_mcp,
        )

        new_mcp = plugin.add_mcp_config_to(base_mcp)

        assert "mcpServers" in new_mcp
        assert "server1" in new_mcp["mcpServers"]
        assert "server2" in new_mcp["mcpServers"]

    def test_add_mcp_config_to_mcp_servers_plugin_overrides(self):
        """Test plugin mcpServers override base mcpServers for same server name."""
        base_mcp = {"mcpServers": {"server1": {"command": "base"}}}
        plugin_mcp = {"mcpServers": {"server1": {"command": "plugin"}}}

        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            mcp_config=plugin_mcp,
        )

        new_mcp = plugin.add_mcp_config_to(base_mcp)

        assert new_mcp["mcpServers"]["server1"]["command"] == "plugin"


# Fixtures


@pytest.fixture
def mock_skill():
    """Create a mock skill for testing."""
    return Skill(
        name="test-skill",
        content="Test skill content",
    )


@pytest.fixture
def another_mock_skill():
    """Create another mock skill for testing."""
    return Skill(
        name="another-skill",
        content="Another skill content",
    )


@pytest.fixture
def empty_plugin():
    """Create an empty plugin."""
    return Plugin(
        manifest=PluginManifest(
            name="empty", version="1.0.0", description="Empty plugin"
        ),
        path="/tmp/empty",
    )


@pytest.fixture
def mock_plugin_with_skills(mock_skill, another_mock_skill):
    """Create a plugin with skills."""
    return Plugin(
        manifest=PluginManifest(
            name="test-plugin", version="1.0.0", description="Test plugin"
        ),
        path="/tmp/test",
        skills=[mock_skill, another_mock_skill],
    )


@pytest.fixture
def mock_plugin_with_mcp():
    """Create a plugin with MCP config."""
    return Plugin(
        manifest=PluginManifest(
            name="mcp-plugin", version="1.0.0", description="MCP plugin"
        ),
        path="/tmp/mcp",
        mcp_config={"server1": {"command": "python", "args": ["-m", "server1"]}},
    )
