"""Tests for Plugin loading functionality."""

from pathlib import Path

import pytest

from openhands.sdk.plugin import Plugin, PluginManifest


class TestPluginManifest:
    """Tests for PluginManifest parsing."""

    def test_basic_manifest(self):
        """Test parsing a basic manifest."""
        manifest = PluginManifest(
            name="test-plugin",
            version="1.0.0",
            description="A test plugin",
        )
        assert manifest.name == "test-plugin"
        assert manifest.version == "1.0.0"
        assert manifest.description == "A test plugin"
        assert manifest.author is None

    def test_manifest_with_author_object(self):
        """Test parsing manifest with author as object."""
        from openhands.sdk.plugin.types import PluginAuthor

        manifest = PluginManifest(
            name="test-plugin",
            author=PluginAuthor(name="Test Author", email="test@example.com"),
        )
        assert manifest.author is not None
        assert manifest.author.name == "Test Author"
        assert manifest.author.email == "test@example.com"


class TestPluginLoading:
    """Tests for Plugin.load() functionality."""

    def test_load_plugin_with_manifest(self, tmp_path: Path):
        """Test loading a plugin with a manifest file."""
        # Create plugin structure
        plugin_dir = tmp_path / "test-plugin"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".plugin"
        manifest_dir.mkdir()

        # Write manifest
        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text(
            """{
            "name": "test-plugin",
            "version": "2.0.0",
            "description": "A test plugin"
        }"""
        )

        # Load plugin
        plugin = Plugin.load(plugin_dir)

        assert plugin.name == "test-plugin"
        assert plugin.version == "2.0.0"
        assert plugin.description == "A test plugin"

    def test_load_plugin_with_claude_plugin_dir(self, tmp_path: Path):
        """Test loading a plugin with .claude-plugin directory."""
        plugin_dir = tmp_path / "claude-plugin"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".claude-plugin"
        manifest_dir.mkdir()

        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text(
            """{
            "name": "claude-plugin",
            "version": "1.0.0"
        }"""
        )

        plugin = Plugin.load(plugin_dir)
        assert plugin.name == "claude-plugin"

    def test_load_plugin_without_manifest(self, tmp_path: Path):
        """Test loading a plugin without manifest (infers from directory name)."""
        plugin_dir = tmp_path / "inferred-plugin"
        plugin_dir.mkdir()

        plugin = Plugin.load(plugin_dir)

        assert plugin.name == "inferred-plugin"
        assert plugin.version == "1.0.0"

    def test_load_plugin_with_skills(self, tmp_path: Path):
        """Test loading a plugin with skills."""
        plugin_dir = tmp_path / "skill-plugin"
        plugin_dir.mkdir()

        # Create skills directory
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()

        # Create a skill
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: test-skill
description: A test skill
---

This is a test skill content.
"""
        )

        plugin = Plugin.load(plugin_dir)

        assert len(plugin.skills) == 1
        assert plugin.skills[0].name == "test-skill"

    def test_load_plugin_with_hooks(self, tmp_path: Path):
        """Test loading a plugin with hooks."""
        plugin_dir = tmp_path / "hook-plugin"
        plugin_dir.mkdir()

        # Create hooks directory
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()

        # Create hooks.json
        hooks_json = hooks_dir / "hooks.json"
        hooks_json.write_text(
            """{
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo test"
                            }
                        ]
                    }
                ]
            }
        }"""
        )

        plugin = Plugin.load(plugin_dir)

        assert plugin.hooks is not None
        assert len(plugin.hooks.hooks.get("PreToolUse", [])) == 1

    def test_load_plugin_with_agents(self, tmp_path: Path):
        """Test loading a plugin with agent definitions."""
        plugin_dir = tmp_path / "agent-plugin"
        plugin_dir.mkdir()

        # Create agents directory
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()

        # Create an agent
        agent_md = agents_dir / "test-agent.md"
        agent_md.write_text(
            """---
name: test-agent
description: A test agent. <example>When user asks about testing</example>
model: inherit
tools:
  - Read
  - Write
---

You are a test agent. Help users with testing.
"""
        )

        plugin = Plugin.load(plugin_dir)

        assert len(plugin.agents) == 1
        agent = plugin.agents[0]
        assert agent.name == "test-agent"
        assert agent.model == "inherit"
        assert "Read" in agent.tools
        assert "Write" in agent.tools
        assert len(agent.when_to_use_examples) == 1
        assert "When user asks about testing" in agent.when_to_use_examples[0]
        assert "You are a test agent" in agent.system_prompt

    def test_load_plugin_with_commands(self, tmp_path: Path):
        """Test loading a plugin with command definitions."""
        plugin_dir = tmp_path / "command-plugin"
        plugin_dir.mkdir()

        # Create commands directory
        commands_dir = plugin_dir / "commands"
        commands_dir.mkdir()

        # Create a command
        command_md = commands_dir / "review.md"
        command_md.write_text(
            """---
description: Review code changes
argument-hint: <file-or-directory>
allowed-tools:
  - Read
  - Grep
---

Review the specified code and provide feedback.
"""
        )

        plugin = Plugin.load(plugin_dir)

        assert len(plugin.commands) == 1
        command = plugin.commands[0]
        assert command.name == "review"
        assert command.description == "Review code changes"
        assert command.argument_hint == "<file-or-directory>"
        assert "Read" in command.allowed_tools
        assert "Review the specified code" in command.content

    def test_load_all_plugins(self, tmp_path: Path):
        """Test loading all plugins from a directory."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        # Create multiple plugins
        for i in range(3):
            plugin_dir = plugins_dir / f"plugin-{i}"
            plugin_dir.mkdir()
            manifest_dir = plugin_dir / ".plugin"
            manifest_dir.mkdir()
            manifest_file = manifest_dir / "plugin.json"
            manifest_file.write_text(f'{{"name": "plugin-{i}"}}')

        plugins = Plugin.load_all(plugins_dir)

        assert len(plugins) == 3
        names = {p.name for p in plugins}
        assert names == {"plugin-0", "plugin-1", "plugin-2"}

    def test_load_nonexistent_plugin(self, tmp_path: Path):
        """Test loading a nonexistent plugin raises error."""
        with pytest.raises(FileNotFoundError):
            Plugin.load(tmp_path / "nonexistent")

    def test_load_plugin_with_invalid_manifest(self, tmp_path: Path):
        """Test loading a plugin with invalid manifest raises error."""
        plugin_dir = tmp_path / "invalid-plugin"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".plugin"
        manifest_dir.mkdir()

        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text("not valid json")

        with pytest.raises(ValueError, match="Invalid JSON"):
            Plugin.load(plugin_dir)
