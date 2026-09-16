"""Package paths must resolve inside the plugin root (Agent Plugins §4.1)."""

import json
from pathlib import Path

import pytest

from openhands.sdk.plugin import AgentPluginsFormat, ClaudeCodePluginFormat
from openhands.sdk.plugin.format.agent_plugins import (
    EXTENSION_NAMESPACE,
    MANIFEST_SCHEMA_URL,
)


SKILL = "---\nname: {name}\ndescription: A skill\n---\nBody\n"
AGENT = "---\nname: {name}\ndescription: An agent\n---\nPrompt\n"
COMMAND = "---\ndescription: A command\n---\nDo it\n"
HOOKS = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "echo hi"}]}
        ]
    }
}


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory next to (not inside) the plugin root."""
    path = tmp_path / "outside"
    path.mkdir()
    return path


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "plugin"
    path.mkdir()
    return path


def test_manifest_escaping_root_rejects_claude_code_plugin(plugin_dir, outside):
    (outside / "plugin.json").write_text(json.dumps({"name": "evil"}))
    (plugin_dir / ".plugin").mkdir()
    (plugin_dir / ".plugin" / "plugin.json").symlink_to(outside / "plugin.json")

    with pytest.raises(ValueError, match="outside the plugin root"):
        ClaudeCodePluginFormat().load(plugin_dir)


def test_manifest_escaping_root_rejects_agent_plugins_plugin(plugin_dir, outside):
    manifest = {"$schema": MANIFEST_SCHEMA_URL, "name": "evil", "version": "1.0.0"}
    (outside / "plugin.json").write_text(json.dumps(manifest))
    (plugin_dir / "plugin.json").symlink_to(outside / "plugin.json")

    assert AgentPluginsFormat.detect(plugin_dir)
    with pytest.raises(ValueError, match="outside the plugin root"):
        AgentPluginsFormat().load(plugin_dir)


def test_skills_dir_escaping_root_disables_skills(plugin_dir, outside):
    (outside / "a").mkdir()
    (outside / "a" / "SKILL.md").write_text(SKILL.format(name="a"))
    (plugin_dir / "skills").symlink_to(outside, target_is_directory=True)

    assert ClaudeCodePluginFormat().load_skills(plugin_dir) == []


def test_skills_that_is_not_a_directory_disables_skills(plugin_dir):
    (plugin_dir / "skills").write_text("not a directory")
    # Without the kind check this would fall through to the root SKILL.md.
    (plugin_dir / "SKILL.md").write_text(SKILL.format(name="root"))

    assert ClaudeCodePluginFormat().load_skills(plugin_dir) == []


def test_escaping_skill_is_skipped_and_siblings_load(plugin_dir, outside):
    skills = plugin_dir / "skills"
    (skills / "good").mkdir(parents=True)
    (skills / "good" / "SKILL.md").write_text(SKILL.format(name="good"))
    (outside / "SKILL.md").write_text(SKILL.format(name="evil"))
    (skills / "evil").mkdir()
    (skills / "evil" / "SKILL.md").symlink_to(outside / "SKILL.md")
    (skills / "flat.md").symlink_to(outside / "SKILL.md")

    loaded = ClaudeCodePluginFormat().load_skills(plugin_dir)

    assert [s.name for s in loaded] == ["good"]


def test_root_skill_escaping_root_is_skipped(plugin_dir, outside):
    (outside / "SKILL.md").write_text(SKILL.format(name="evil"))
    (plugin_dir / "SKILL.md").symlink_to(outside / "SKILL.md")

    assert ClaudeCodePluginFormat().load_skills(plugin_dir) == []


def test_symlink_within_root_is_allowed(plugin_dir):
    (plugin_dir / "shared").mkdir()
    (plugin_dir / "shared" / "SKILL.md").write_text(SKILL.format(name="linked"))
    (plugin_dir / "skills").mkdir()
    (plugin_dir / "skills" / "linked").symlink_to(
        plugin_dir / "shared", target_is_directory=True
    )

    loaded = ClaudeCodePluginFormat().load_skills(plugin_dir)

    assert [s.name for s in loaded] == ["linked"]


def test_mcp_config_escaping_root_is_ignored(plugin_dir, outside):
    config = {"mcpServers": {"evil": {"command": "echo"}}}
    (outside / "mcp.json").write_text(json.dumps(config))
    (plugin_dir / ".mcp.json").symlink_to(outside / "mcp.json")

    assert ClaudeCodePluginFormat().load_mcp_config(plugin_dir) == {}


def test_mcp_config_that_is_not_a_file_is_ignored(plugin_dir):
    (plugin_dir / ".mcp.json").mkdir()

    assert ClaudeCodePluginFormat().load_mcp_config(plugin_dir) == {}


def test_hooks_escaping_root_are_ignored(plugin_dir, outside):
    (outside / "hooks.json").write_text(json.dumps(HOOKS))
    (plugin_dir / "hooks").mkdir()
    (plugin_dir / "hooks" / "hooks.json").symlink_to(outside / "hooks.json")

    assert ClaudeCodePluginFormat().load_hooks(plugin_dir) is None


def test_escaping_agent_and_command_files_are_skipped(plugin_dir, outside):
    (outside / "evil-agent.md").write_text(AGENT.format(name="evil"))
    (outside / "evil-command.md").write_text(COMMAND)
    for kind, good, evil in [
        ("agents", AGENT.format(name="good"), "evil-agent.md"),
        ("commands", COMMAND, "evil-command.md"),
    ]:
        (plugin_dir / kind).mkdir()
        (plugin_dir / kind / "good.md").write_text(good)
        (plugin_dir / kind / "evil.md").symlink_to(outside / evil)

    fmt = ClaudeCodePluginFormat()

    assert [a.name for a in fmt.load_agents(plugin_dir)] == ["good"]
    assert [c.name for c in fmt.load_commands(plugin_dir)] == ["good"]


def test_extension_directory_escaping_root_is_denied(plugin_dir, outside):
    (outside / "hooks").mkdir()
    (outside / "hooks" / "hooks.json").write_text(json.dumps(HOOKS))
    (outside / "agents").mkdir()
    (outside / "agents" / "evil.md").write_text(AGENT.format(name="evil"))
    (outside / "commands").mkdir()
    (outside / "commands" / "evil.md").write_text(COMMAND)
    (plugin_dir / EXTENSION_NAMESPACE).symlink_to(outside, target_is_directory=True)

    fmt = AgentPluginsFormat()

    assert fmt.load_hooks(plugin_dir) is None
    assert fmt.load_agents(plugin_dir) == []
    assert fmt.load_commands(plugin_dir) == []
