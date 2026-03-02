"""Tests for Plugin loading functionality."""

from pathlib import Path

import pytest

from openhands.sdk.plugin import Plugin, PluginManifest
from openhands.sdk.plugin.types import (
    CommandDefinition,
    PluginAuthor,
)


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

    def test_manifest_with_entry_command(self):
        """Test parsing manifest with entry_command field."""
        manifest = PluginManifest(
            name="city-weather",
            version="1.0.0",
            entry_command="now",
        )
        assert manifest.name == "city-weather"
        assert manifest.entry_command == "now"

    def test_manifest_without_entry_command(self):
        """Test that entry_command defaults to None."""
        manifest = PluginManifest(name="test-plugin")
        assert manifest.entry_command is None


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
        assert not plugin.hooks.is_empty()
        assert len(plugin.hooks.pre_tool_use) == 1

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

    def test_load_plugin_with_entry_command(self, tmp_path: Path):
        """Test loading a plugin with entry_command in manifest."""
        plugin_dir = tmp_path / "city-weather"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".plugin"
        manifest_dir.mkdir()

        # Write manifest with entry_command
        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text(
            """{
            "name": "city-weather",
            "version": "1.0.0",
            "description": "Get current weather for any city",
            "entry_command": "now"
        }"""
        )

        plugin = Plugin.load(plugin_dir)

        assert plugin.name == "city-weather"
        assert plugin.manifest.entry_command == "now"
        assert plugin.entry_slash_command == "/city-weather:now"

    def test_load_plugin_without_entry_command(self, tmp_path: Path):
        """Test that entry_slash_command returns None when no entry_command is set."""
        plugin_dir = tmp_path / "test-plugin"
        plugin_dir.mkdir()

        plugin = Plugin.load(plugin_dir)

        assert plugin.manifest.entry_command is None
        assert plugin.entry_slash_command is None

    def test_command_to_skill_conversion(self, tmp_path: Path):
        """Test converting a command to a keyword-triggered skill."""
        from openhands.sdk.context.skills.trigger import KeywordTrigger

        plugin_dir = tmp_path / "city-weather"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".plugin"
        manifest_dir.mkdir()
        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text('{"name": "city-weather", "version": "1.0.0"}')

        commands_dir = plugin_dir / "commands"
        commands_dir.mkdir()
        command_md = commands_dir / "now.md"
        command_md.write_text(
            """---
description: Get current weather for a city
argument-hint: <city-name>
allowed-tools:
  - tavily_search
---

Fetch and display the current weather for the specified city.
"""
        )

        plugin = Plugin.load(plugin_dir)
        assert len(plugin.commands) == 1

        # Convert command to skill
        command = plugin.commands[0]
        skill = command.to_skill("city-weather")

        # Verify skill properties
        assert skill.name == "city-weather:now"
        assert skill.description == "Get current weather for a city"
        assert skill.allowed_tools is not None
        assert "tavily_search" in skill.allowed_tools

        # Verify trigger format
        assert isinstance(skill.trigger, KeywordTrigger)
        assert "/city-weather:now" in skill.trigger.keywords

        # Verify content includes argument hint
        assert "$ARGUMENTS" in skill.content
        assert "Fetch and display the current weather" in skill.content

    def test_get_all_skills_with_commands(self, tmp_path: Path):
        """Test get_all_skills returns both skills and command-derived skills."""
        from openhands.sdk.context.skills.trigger import KeywordTrigger

        plugin_dir = tmp_path / "test-plugin"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".plugin"
        manifest_dir.mkdir()
        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text('{"name": "test-plugin", "version": "1.0.0"}')

        # Create skills directory with a skill
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: my-skill
description: A regular skill
---

This is a regular skill content.
"""
        )

        # Create commands directory with a command
        commands_dir = plugin_dir / "commands"
        commands_dir.mkdir()
        command_md = commands_dir / "greet.md"
        command_md.write_text(
            """---
description: Greet someone
argument-hint: <name>
---

Say hello to the specified person.
"""
        )

        plugin = Plugin.load(plugin_dir)

        # Verify separate counts
        assert len(plugin.skills) == 1
        assert len(plugin.commands) == 1

        # Verify combined skills
        all_skills = plugin.get_all_skills()
        assert len(all_skills) == 2

        # Find the regular skill and command-derived skill
        skill_names = {s.name for s in all_skills}
        assert "my-skill" in skill_names
        assert "test-plugin:greet" in skill_names

        # Verify command-derived skill has keyword trigger
        command_skill = next(s for s in all_skills if s.name == "test-plugin:greet")
        assert isinstance(command_skill.trigger, KeywordTrigger)
        assert "/test-plugin:greet" in command_skill.trigger.keywords

    def test_get_all_skills_empty_commands(self, tmp_path: Path):
        """Test get_all_skills with no commands."""
        plugin_dir = tmp_path / "no-commands"
        plugin_dir.mkdir()

        # Create skills directory with a skill only
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "only-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: only-skill
description: The only skill
---

Content for the only skill.
"""
        )

        plugin = Plugin.load(plugin_dir)

        all_skills = plugin.get_all_skills()
        assert len(all_skills) == 1
        assert all_skills[0].name == "only-skill"

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

    def test_load_all_nonexistent_directory(self, tmp_path: Path):
        """Test load_all with nonexistent directory returns empty list."""
        plugins = Plugin.load_all(tmp_path / "nonexistent")
        assert plugins == []

    def test_load_all_with_failing_plugin(self, tmp_path: Path):
        """Test load_all continues when a plugin fails to load (lines 197-198)."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        # Create a valid plugin
        valid_dir = plugins_dir / "valid-plugin"
        valid_dir.mkdir()
        manifest_dir = valid_dir / ".plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text('{"name": "valid-plugin"}')

        # Create an invalid plugin (will fail to load)
        invalid_dir = plugins_dir / "invalid-plugin"
        invalid_dir.mkdir()
        invalid_manifest_dir = invalid_dir / ".plugin"
        invalid_manifest_dir.mkdir()
        (invalid_manifest_dir / "plugin.json").write_text("not valid json")

        plugins = Plugin.load_all(plugins_dir)

        # Should load the valid plugin and skip the invalid one
        assert len(plugins) == 1
        assert plugins[0].name == "valid-plugin"

    def test_load_plugin_with_author_string(self, tmp_path: Path):
        """Test loading manifest with author as string (line 225)."""
        plugin_dir = tmp_path / "author-plugin"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".plugin"
        manifest_dir.mkdir()

        # Write manifest with author as string
        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text(
            """{
            "name": "author-plugin",
            "version": "1.0.0",
            "author": "Test Author <test@example.com>"
        }"""
        )

        plugin = Plugin.load(plugin_dir)

        assert plugin.name == "author-plugin"
        assert plugin.manifest.author is not None
        assert plugin.manifest.author.name == "Test Author"
        assert plugin.manifest.author.email == "test@example.com"

    def test_load_plugin_with_manifest_parse_error(self, tmp_path: Path):
        """Test loading manifest with parse error (lines 230-231)."""
        plugin_dir = tmp_path / "error-plugin"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".plugin"
        manifest_dir.mkdir()

        # Write manifest with missing required field or wrong type
        # This will parse as JSON but fail Pydantic validation
        manifest_file = manifest_dir / "plugin.json"
        manifest_file.write_text('{"name": 123}')  # name should be string

        with pytest.raises(ValueError, match="Failed to parse manifest"):
            Plugin.load(plugin_dir)


class TestPluginAuthor:
    """Tests for PluginAuthor parsing."""

    def test_from_string_with_email(self):
        """Test parsing author string with email (lines 22-25)."""
        author = PluginAuthor.from_string("John Doe <john@example.com>")
        assert author.name == "John Doe"
        assert author.email == "john@example.com"

    def test_from_string_without_email(self):
        """Test parsing author string without email (line 26)."""
        author = PluginAuthor.from_string("John Doe")
        assert author.name == "John Doe"
        assert author.email is None

    def test_from_string_with_whitespace(self):
        """Test parsing author string with extra whitespace."""
        author = PluginAuthor.from_string("  John Doe  <  john@example.com  >  ")
        assert author.name == "John Doe"
        assert author.email == "john@example.com"


class TestCommandDefinition:
    """Tests for CommandDefinition loading."""

    def test_load_command_basic(self, tmp_path: Path):
        """Test loading a basic command definition (lines 184-218)."""
        command_md = tmp_path / "review.md"
        command_md.write_text(
            """---
description: Review code
argument-hint: <file>
allowed-tools:
  - Read
  - Grep
---

Review the specified file.
"""
        )

        command = CommandDefinition.load(command_md)

        assert command.name == "review"
        assert command.description == "Review code"
        assert command.argument_hint == "<file>"
        assert command.allowed_tools == ["Read", "Grep"]
        assert command.content == "Review the specified file."

    def test_load_command_with_argument_hint_list(self, tmp_path: Path):
        """Test loading command with argument-hint as list."""
        command_md = tmp_path / "multi-arg.md"
        command_md.write_text(
            """---
description: Multi arg command
argument-hint:
  - <file>
  - <options>
---

Content.
"""
        )

        command = CommandDefinition.load(command_md)
        assert command.argument_hint == "<file> <options>"

    def test_load_command_with_camel_case_fields(self, tmp_path: Path):
        """Test loading command with camelCase field names."""
        command_md = tmp_path / "camel.md"
        command_md.write_text(
            """---
description: Camel case command
argumentHint: <arg>
allowedTools:
  - Tool1
---

Content.
"""
        )

        command = CommandDefinition.load(command_md)
        assert command.argument_hint == "<arg>"
        assert command.allowed_tools == ["Tool1"]

    def test_load_command_with_allowed_tools_as_string(self, tmp_path: Path):
        """Test loading command with allowed-tools as string."""
        command_md = tmp_path / "single-tool.md"
        command_md.write_text(
            """---
description: Single tool
allowed-tools: Read
---

Content.
"""
        )

        command = CommandDefinition.load(command_md)
        assert command.allowed_tools == ["Read"]

    def test_load_command_defaults(self, tmp_path: Path):
        """Test command defaults when fields not provided."""
        command_md = tmp_path / "minimal.md"
        command_md.write_text(
            """---
---

Just instructions.
"""
        )

        command = CommandDefinition.load(command_md)
        assert command.name == "minimal"
        assert command.description == ""
        assert command.argument_hint is None
        assert command.allowed_tools == []

    def test_load_command_with_metadata(self, tmp_path: Path):
        """Test loading command with extra metadata."""
        command_md = tmp_path / "meta.md"
        command_md.write_text(
            """---
description: Meta command
custom_field: custom_value
---

Content.
"""
        )

        command = CommandDefinition.load(command_md)
        assert command.metadata.get("custom_field") == "custom_value"
