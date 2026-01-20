"""Tests for plugin merging utilities."""

import pytest

from openhands.sdk.context import AgentContext
from openhands.sdk.context.skills import Skill
from openhands.sdk.plugin import Plugin, PluginManifest
from openhands.sdk.plugin.utils import merge_mcp_configs, merge_skills


class TestMergeSkills:
    """Tests for merge_skills utility function."""

    def test_merge_skills_empty_plugin_skills(self):
        """Test merging empty plugin skills returns context unchanged."""
        context = AgentContext(skills=[])
        result = merge_skills(context, [])
        assert result.skills == []

    def test_merge_skills_none_context_empty_plugin(self):
        """Test merging with None context and empty plugin creates empty context."""
        result = merge_skills(None, [])
        assert isinstance(result, AgentContext)
        assert result.skills == []

    def test_merge_skills_none_context_with_plugin_skills(self, mock_skill):
        """Test merging with None context creates new context with plugin skills."""
        plugin_skills = [mock_skill]
        result = merge_skills(None, plugin_skills)
        assert isinstance(result, AgentContext)
        assert len(result.skills) == 1
        assert result.skills[0].name == mock_skill.name

    def test_merge_skills_adds_new_skill(self, mock_skill, another_mock_skill):
        """Test merging adds new skill when no conflict."""
        context = AgentContext(skills=[mock_skill])
        result = merge_skills(context, [another_mock_skill])
        assert len(result.skills) == 2
        skill_names = {s.name for s in result.skills}
        assert skill_names == {mock_skill.name, another_mock_skill.name}

    def test_merge_skills_overrides_existing_skill(self, mock_skill):
        """Test plugin skill overrides existing skill with same name."""
        original_skill = Skill(
            name="test-skill",
            content="Original content",
        )
        context = AgentContext(skills=[original_skill])

        updated_skill = Skill(
            name="test-skill",
            content="Updated content",
        )

        result = merge_skills(context, [updated_skill])
        assert len(result.skills) == 1
        assert result.skills[0].content == "Updated content"

    def test_merge_skills_preserves_insertion_order(
        self, mock_skill, another_mock_skill
    ):
        """Test merge preserves order of existing skills."""
        skill_a = Skill(name="skill-a", content="A")
        skill_b = Skill(name="skill-b", content="B")
        context = AgentContext(skills=[skill_a, skill_b])

        # Add new skill at the end
        skill_c = Skill(name="skill-c", content="C")
        result = merge_skills(context, [skill_c])

        skill_names = [s.name for s in result.skills]
        assert skill_names == ["skill-a", "skill-b", "skill-c"]

    def test_merge_skills_returns_new_context(self, mock_skill):
        """Test merge returns new context instance, not modifying original."""
        original_context = AgentContext(skills=[mock_skill])
        new_skill = Skill(name="new-skill", content="New")

        result = merge_skills(original_context, [new_skill])

        # Original context should be unchanged
        assert len(original_context.skills) == 1
        assert len(result.skills) == 2
        assert result is not original_context


class TestMergeMCPConfigs:
    """Tests for merge_mcp_configs utility function."""

    def test_merge_mcp_configs_both_none(self):
        """Test merging two None configs returns empty dict."""
        result = merge_mcp_configs(None, None)
        assert result == {}

    def test_merge_mcp_configs_base_none(self):
        """Test merging with None base returns plugin config."""
        plugin_config = {"server1": {"command": "python", "args": ["-m", "server1"]}}
        result = merge_mcp_configs(None, plugin_config)
        assert result == plugin_config

    def test_merge_mcp_configs_plugin_none(self):
        """Test merging with None plugin returns base config."""
        base_config = {"server1": {"command": "python", "args": ["-m", "server1"]}}
        result = merge_mcp_configs(base_config, None)
        assert result == base_config

    def test_merge_mcp_configs_both_empty(self):
        """Test merging two empty dicts returns empty dict."""
        result = merge_mcp_configs({}, {})
        assert result == {}

    def test_merge_mcp_configs_no_conflicts(self):
        """Test merging configs with different keys combines them."""
        base_config = {"server1": {"command": "python", "args": ["-m", "server1"]}}
        plugin_config = {"server2": {"command": "node", "args": ["server2.js"]}}
        result = merge_mcp_configs(base_config, plugin_config)
        assert len(result) == 2
        assert "server1" in result
        assert "server2" in result

    def test_merge_mcp_configs_plugin_overrides(self):
        """Test plugin config overrides base config for same key."""
        base_config = {"server1": {"command": "python", "args": ["-m", "base_server"]}}
        plugin_config = {
            "server1": {"command": "python", "args": ["-m", "plugin_server"]}
        }
        result = merge_mcp_configs(base_config, plugin_config)
        assert result["server1"]["args"] == ["-m", "plugin_server"]

    def test_merge_mcp_configs_does_not_modify_inputs(self):
        """Test merge does not modify input dicts."""
        base_config = {"server1": {"command": "python"}}
        plugin_config = {"server2": {"command": "node"}}
        original_base = base_config.copy()
        original_plugin = plugin_config.copy()

        merge_mcp_configs(base_config, plugin_config)

        assert base_config == original_base
        assert plugin_config == original_plugin


class TestPluginMergeInto:
    """Tests for Plugin.merge_into() method."""

    def test_merge_into_empty_plugin(self, empty_plugin):
        """Test merging an empty plugin returns unchanged contexts."""
        context = AgentContext(skills=[])
        mcp_config = {}

        new_context, new_mcp = empty_plugin.merge_into(context, mcp_config)

        assert new_context.skills == []
        assert new_mcp == {}

    def test_merge_into_none_inputs(self, mock_plugin_with_skills):
        """Test merging with None inputs creates new contexts."""
        new_context, new_mcp = mock_plugin_with_skills.merge_into()

        assert isinstance(new_context, AgentContext)
        assert isinstance(new_mcp, dict)
        assert len(new_context.skills) > 0

    def test_merge_into_with_skills(self, mock_plugin_with_skills):
        """Test merging plugin with skills."""
        context = AgentContext(skills=[])
        new_context, new_mcp = mock_plugin_with_skills.merge_into(context, {})

        assert len(new_context.skills) == len(mock_plugin_with_skills.skills)

    def test_merge_into_with_mcp_config(self, mock_plugin_with_mcp):
        """Test merging plugin with MCP config."""
        context = AgentContext(skills=[])
        new_context, new_mcp = mock_plugin_with_mcp.merge_into(context, {})

        assert new_mcp == mock_plugin_with_mcp.mcp_config

    def test_merge_into_enforces_max_skills(self, mock_plugin_with_skills):
        """Test merge_into enforces max_skills limit."""
        context = AgentContext(skills=[])

        # Should raise ValueError if limit is exceeded
        with pytest.raises(ValueError, match="exceeds maximum"):
            mock_plugin_with_skills.merge_into(context, {}, max_skills=0)

    def test_merge_into_max_skills_with_existing_skills(self, mock_skill):
        """Test max_skills counts unique skills after merge."""
        # Create plugin with 2 skills
        plugin_skill_1 = Skill(name="plugin-skill-1", content="P1")
        plugin_skill_2 = Skill(name="plugin-skill-2", content="P2")
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[plugin_skill_1, plugin_skill_2],
        )

        # Existing context has 1 skill
        context = AgentContext(skills=[mock_skill])

        # Limit of 3 should allow merge (1 existing + 2 new = 3)
        new_context, _ = plugin.merge_into(context, {}, max_skills=3)
        assert len(new_context.skills) == 3

        # Limit of 2 should fail (3 > 2)
        with pytest.raises(ValueError, match="exceeds maximum"):
            plugin.merge_into(context, {}, max_skills=2)

    def test_merge_into_max_skills_with_override(self):
        """Test max_skills counts correctly when plugin overrides existing skill."""
        # Create existing skill
        existing_skill = Skill(name="shared-skill", content="Old")
        context = AgentContext(skills=[existing_skill])

        # Plugin has skill with same name (will override)
        plugin_skill = Skill(name="shared-skill", content="New")
        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            skills=[plugin_skill],
        )

        # Should succeed with limit of 1 (only 1 unique skill after merge)
        new_context, _ = plugin.merge_into(context, {}, max_skills=1)
        assert len(new_context.skills) == 1
        assert new_context.skills[0].content == "New"

    def test_merge_into_preserves_context_fields(self, mock_plugin_with_skills):
        """Test merge preserves other AgentContext fields."""
        context = AgentContext(
            skills=[],
            system_message_suffix="Custom suffix",
        )

        new_context, _ = mock_plugin_with_skills.merge_into(context, {})

        assert new_context.system_message_suffix == context.system_message_suffix

    def test_merge_into_returns_merged_mcp_config(self):
        """Test merge returns correctly merged MCP config."""
        base_mcp = {"server1": {"command": "base"}}
        plugin_mcp = {"server2": {"command": "plugin"}}

        plugin = Plugin(
            manifest=PluginManifest(name="test", version="1.0.0", description="Test"),
            path="/tmp/test",
            mcp_config=plugin_mcp,
        )

        _, new_mcp = plugin.merge_into(None, base_mcp)

        assert "server1" in new_mcp
        assert "server2" in new_mcp
        assert new_mcp["server1"]["command"] == "base"
        assert new_mcp["server2"]["command"] == "plugin"


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
