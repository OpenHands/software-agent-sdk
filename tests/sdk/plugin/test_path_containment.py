"""Package paths must resolve inside the plugin root (Agent Plugins §4.1)."""

import json
from pathlib import Path

import pytest

from openhands.sdk.plugin import AgentPluginsFormat, ClaudeCodePluginFormat, Plugin
from openhands.sdk.plugin.format.agent_plugins import (
    EXTENSION_NAMESPACE,
    MANIFEST_SCHEMA_URL,
)
from openhands.sdk.skills import utils as skills_utils


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
MCP = {"mcpServers": {"evil": {"command": "echo"}}}


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


def write_skill(skills_dir: Path, name: str) -> Path:
    (skills_dir / name).mkdir(parents=True)
    skill_md = skills_dir / name / "SKILL.md"
    skill_md.write_text(SKILL.format(name=name))
    return skill_md


def test_manifest_escaping_root_rejects_claude_code_plugin(plugin_dir, outside):
    (outside / "plugin.json").write_text(json.dumps({"name": "evil"}))
    (plugin_dir / ".plugin").mkdir()
    (plugin_dir / ".plugin" / "plugin.json").symlink_to(outside / "plugin.json")

    with pytest.raises(ValueError, match="resolves outside the plugin root"):
        ClaudeCodePluginFormat().load(plugin_dir)


def test_dangling_escaping_manifest_rejects_plugin(plugin_dir, outside):
    (plugin_dir / ".plugin").mkdir()
    (plugin_dir / ".plugin" / "plugin.json").symlink_to(outside / "missing.json")

    # Without the check this loads under a name inferred from the directory.
    with pytest.raises(ValueError, match="resolves outside the plugin root"):
        ClaudeCodePluginFormat().load(plugin_dir)


def test_manifest_escaping_root_rejects_agent_plugins_plugin(plugin_dir, outside):
    manifest = {"$schema": MANIFEST_SCHEMA_URL, "name": "evil", "version": "1.0.0"}
    (outside / "plugin.json").write_text(json.dumps(manifest))
    (plugin_dir / "plugin.json").symlink_to(outside / "plugin.json")

    assert AgentPluginsFormat.detect(plugin_dir)
    with pytest.raises(ValueError, match="resolves outside the plugin root"):
        AgentPluginsFormat().load(plugin_dir)


def test_escaping_paths_are_denied_while_the_rest_loads(plugin_dir, outside):
    write_skill(plugin_dir / "skills", "good")
    write_skill(outside, "evil")
    (plugin_dir / "skills" / "evil").symlink_to(outside / "evil")
    (plugin_dir / "skills" / "flat.md").symlink_to(outside / "evil" / "SKILL.md")
    (outside / "mcp.json").write_text(json.dumps(MCP))
    (plugin_dir / ".mcp.json").symlink_to(outside / "mcp.json")
    (outside / "hooks.json").write_text(json.dumps(HOOKS))
    (plugin_dir / "hooks").mkdir()
    (plugin_dir / "hooks" / "hooks.json").symlink_to(outside / "hooks.json")

    plugin = Plugin.load(plugin_dir)

    assert [s.name for s in plugin.skills] == ["good"]
    assert plugin.mcp_config == {}
    assert plugin.hooks is None


def test_escaping_skill_directory_is_not_listed(plugin_dir, outside, monkeypatch):
    write_skill(outside, "evil")
    (plugin_dir / "skills").mkdir()
    (plugin_dir / "skills" / "evil").symlink_to(outside / "evil")
    listed: list[Path] = []
    real = skills_utils.find_skill_md
    monkeypatch.setattr(
        skills_utils, "find_skill_md", lambda d: listed.append(d) or real(d)
    )

    assert ClaudeCodePluginFormat().load_skills(plugin_dir) == []
    assert plugin_dir / "skills" / "evil" not in listed


def test_escaping_skill_mcp_config_is_ignored_but_skill_loads(plugin_dir, outside):
    skill_md = write_skill(plugin_dir / "skills", "good")
    (outside / "mcp.json").write_text(json.dumps(MCP))
    (skill_md.parent / ".mcp.json").symlink_to(outside / "mcp.json")

    [skill] = ClaudeCodePluginFormat().load_skills(plugin_dir)

    assert skill.name == "good"
    assert not skill.mcp_tools


def test_skills_dir_escaping_root_disables_skills(plugin_dir, outside):
    write_skill(outside, "a")
    (plugin_dir / "skills").symlink_to(outside, target_is_directory=True)

    assert ClaudeCodePluginFormat().load_skills(plugin_dir) == []


def test_skills_that_is_not_a_directory_disables_skills(plugin_dir):
    (plugin_dir / "skills").write_text("not a directory")
    # Without the kind check this would fall through to the root SKILL.md.
    (plugin_dir / "SKILL.md").write_text(SKILL.format(name="root"))

    assert ClaudeCodePluginFormat().load_skills(plugin_dir) == []


def test_root_skill_escaping_root_is_skipped(plugin_dir, outside):
    (outside / "SKILL.md").write_text(SKILL.format(name="evil"))
    (plugin_dir / "SKILL.md").symlink_to(outside / "SKILL.md")

    assert ClaudeCodePluginFormat().load_skills(plugin_dir) == []


@pytest.mark.parametrize(
    ("kind", "content", "loader"),
    [
        ("agents", AGENT.format(name="x"), ClaudeCodePluginFormat.load_agents),
        ("commands", COMMAND, ClaudeCodePluginFormat.load_commands),
    ],
)
def test_escaping_agent_and_command_files_are_skipped(
    plugin_dir, outside, kind, content, loader
):
    (outside / "evil.md").write_text(content.replace("name: x", "name: evil"))
    (plugin_dir / kind).mkdir()
    (plugin_dir / kind / "good.md").write_text(content.replace("name: x", "name: good"))
    (plugin_dir / kind / "evil.md").symlink_to(outside / "evil.md")

    loaded = loader(ClaudeCodePluginFormat(), plugin_dir)

    assert [item.name for item in loaded] == ["good"]


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


def test_symlink_within_root_is_allowed(plugin_dir):
    write_skill(plugin_dir / "shared", "linked")
    (plugin_dir / "skills").mkdir()
    (plugin_dir / "skills" / "linked").symlink_to(
        plugin_dir / "shared" / "linked", target_is_directory=True
    )

    loaded = ClaudeCodePluginFormat().load_skills(plugin_dir)

    assert [s.name for s in loaded] == ["linked"]


def test_plugin_root_reached_through_a_symlink_loads(plugin_dir, tmp_path):
    write_skill(plugin_dir / "skills", "good")
    (plugin_dir / ".mcp.json").write_text(json.dumps(MCP))
    link = tmp_path / "link"
    link.symlink_to(plugin_dir, target_is_directory=True)

    # format.load() does not resolve its argument, unlike Plugin.load().
    plugin = ClaudeCodePluginFormat().load(link)

    assert [s.name for s in plugin.skills] == ["good"]
    assert list(plugin.mcp_config) == ["evil"]


def test_unresolvable_path_is_skipped_not_fatal(plugin_dir, monkeypatch):
    # Python 3.12 raises RuntimeError resolving a symlink loop (3.13 does not).
    real_resolve = Path.resolve

    def resolve(self, strict=False):
        if self.name == "loop.md":
            raise RuntimeError("Symlink loop")
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    (plugin_dir / "agents").mkdir()
    (plugin_dir / "agents" / "good.md").write_text(AGENT.format(name="good"))
    (plugin_dir / "agents" / "loop.md").symlink_to("loop.md")

    plugin = Plugin.load(plugin_dir)

    assert [a.name for a in plugin.agents] == ["good"]


def write_agent_plugin(plugin_dir: Path) -> None:
    manifest = {"$schema": MANIFEST_SCHEMA_URL, "name": "demo", "version": "1.0.0"}
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest))


def test_agent_plugins_mcp_json_escaping_root_disables_mcp(
    plugin_dir, outside, tmp_path
):
    write_agent_plugin(plugin_dir)
    document = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"evil": {"type": "stdio", "command": "echo"}},
    }
    (outside / "mcp.json").write_text(json.dumps(document))
    (plugin_dir / "mcp.json").symlink_to(outside / "mcp.json")

    fmt = AgentPluginsFormat(plugin_data_root=tmp_path / "data")

    assert fmt.load_mcp_config(plugin_dir) == {}


def test_unresolvable_mcp_command_skips_only_that_server(
    plugin_dir, tmp_path, monkeypatch
):
    write_agent_plugin(plugin_dir)
    document = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "loop": {"type": "stdio", "command": "./loop"},
            "ok": {"type": "stdio", "command": "echo"},
        },
    }
    (plugin_dir / "mcp.json").write_text(json.dumps(document))
    real_resolve = Path.resolve

    def resolve(self, strict=False):
        if self.name == "loop":
            raise RuntimeError("Symlink loop")
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    fmt = AgentPluginsFormat(plugin_data_root=tmp_path / "data")

    assert list(fmt.load_mcp_config(plugin_dir)) == ["ok"]


@pytest.mark.parametrize("layout", ["skills_dir", "root_skill"])
def test_escaping_skill_resources_are_dropped(plugin_dir, outside, layout):
    skill_dir = plugin_dir / "skills" / "good" if layout == "skills_dir" else plugin_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL.format(name="good"))
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "ok.sh").write_text("echo ok")
    (outside / "secret.sh").write_text("echo secret")
    (skill_dir / "scripts" / "evil.sh").symlink_to(outside / "secret.sh")
    (outside / "refs").mkdir()
    (outside / "refs" / "leak.md").write_text("leak")
    (skill_dir / "references").symlink_to(outside / "refs", target_is_directory=True)

    [skill] = ClaudeCodePluginFormat().load_skills(plugin_dir)

    assert skill.resources is not None
    assert skill.resources.scripts == ["ok.sh"]
    assert skill.resources.references == []
