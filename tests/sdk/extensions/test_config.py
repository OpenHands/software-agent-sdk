"""Tests for ExtensionConfig and ResolvedExtensions.

Phase 1 of the extensions-config migration: tests written first (TDD),
implementation follows in Phase 2.
"""

import json
from pathlib import Path

from openhands.sdk.extensions.config import ExtensionConfig, ResolvedExtensions
from openhands.sdk.hooks import HookConfig
from openhands.sdk.hooks.config import HookDefinition, HookMatcher
from openhands.sdk.plugin import PluginSource
from openhands.sdk.skills.skill import Skill


def _make_hook_config() -> HookConfig:
    """Create a minimal HookConfig for testing."""
    return HookConfig(
        session_start=[
            HookMatcher(
                matcher="*",
                hooks=[HookDefinition(command="echo hello")],
            )
        ]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(name: str, description: str = "") -> Skill:
    """Create a minimal Skill for testing."""
    return Skill(
        name=name,
        content=description or f"Skill {name}",
        description=description or f"Skill {name}",
    )


def _create_plugin_dir(
    base: Path,
    name: str,
    *,
    skills: list[dict[str, str]] | None = None,
    mcp_config: dict | None = None,
    hooks: dict | None = None,
    agents: list[dict] | None = None,
) -> Path:
    """Create a real plugin directory on disk for testing."""
    plugin_dir = base / name
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"Test plugin {name}",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest))

    if skills:
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        for s in skills:
            skill_file = skills_dir / f"{s['name']}.md"
            skill_file.write_text(
                f"---\nname: {s['name']}\n---\n{s.get('content', '')}"
            )

    if mcp_config:
        (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_config))

    if hooks:
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        # HookConfig expects event-type keys (session_start, pre_tool_use, etc.)
        # with lists of HookMatcher objects
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks))

    if agents:
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir(exist_ok=True)
        for agent_def in agents:
            agent_name = agent_def["name"]
            agent_file = agents_dir / f"{agent_name}.md"
            agent_file.write_text(
                f"---\nname: {agent_name}\n"
                f"model: test/model\n"
                f"---\n{agent_def.get('content', '')}"
            )

    return plugin_dir


# ---------------------------------------------------------------------------
# Model construction
# ---------------------------------------------------------------------------


def test_default_config():
    """Empty config has sensible defaults."""
    cfg = ExtensionConfig()
    assert cfg.skills == []
    assert cfg.plugins == []
    assert cfg.hook_config is None
    assert cfg.load_user_extensions is False
    assert cfg.load_public_extensions is False


def test_config_with_renamed_flags():
    """The renamed flags are accepted and stored."""
    cfg = ExtensionConfig(
        load_user_extensions=True,
        load_public_extensions=True,
    )
    assert cfg.load_user_extensions is True
    assert cfg.load_public_extensions is True


def test_config_with_explicit_skills():
    """Explicit skills are stored on the config."""
    s = _make_skill("my-skill")
    cfg = ExtensionConfig(skills=[s])
    assert len(cfg.skills) == 1
    assert cfg.skills[0].name == "my-skill"


def test_config_with_plugins():
    """PluginSource specs are stored on the config."""
    cfg = ExtensionConfig(plugins=[PluginSource(source="/tmp/fake-plugin")])
    assert len(cfg.plugins) == 1


def test_config_with_hook_config():
    """Explicit hook config is stored."""
    cfg = ExtensionConfig(hook_config=_make_hook_config())
    assert cfg.hook_config is not None


# ---------------------------------------------------------------------------
# ResolvedExtensions model
# ---------------------------------------------------------------------------


def test_resolved_extensions_defaults():
    """ResolvedExtensions has empty defaults."""
    r = ResolvedExtensions()
    assert r.skills == []
    assert r.mcp_config == {}
    assert r.hooks is None
    assert r.agents == []
    assert r.resolved_plugins == []


# ---------------------------------------------------------------------------
# resolve() — no I/O paths (no plugins, no auto-loading)
# ---------------------------------------------------------------------------


def test_resolve_empty_config():
    """Empty config resolves to empty extensions."""
    resolved = ExtensionConfig().resolve()
    assert resolved.skills == []
    assert resolved.mcp_config == {}
    assert resolved.hooks is None
    assert resolved.agents == []
    assert resolved.resolved_plugins == []


def test_resolve_explicit_skills_only():
    """Explicit skills pass through to resolved output."""
    s1 = _make_skill("alpha")
    s2 = _make_skill("beta")
    resolved = ExtensionConfig(skills=[s1, s2]).resolve()

    names = {s.name for s in resolved.skills}
    assert names == {"alpha", "beta"}


def test_resolve_existing_skills_as_lowest_precedence():
    """existing_skills are overridden by explicit config skills."""
    existing = _make_skill("shared", description="old")
    explicit = _make_skill("shared", description="new")

    resolved = ExtensionConfig(skills=[explicit]).resolve(existing_skills=[existing])
    matched = [s for s in resolved.skills if s.name == "shared"]
    assert len(matched) == 1
    assert matched[0].description == "new"


def test_resolve_existing_skills_preserved_when_no_override():
    """existing_skills that aren't overridden appear in the output."""
    existing = _make_skill("only-existing")
    resolved = ExtensionConfig().resolve(existing_skills=[existing])
    assert any(s.name == "only-existing" for s in resolved.skills)


def test_resolve_explicit_hook_config():
    """Explicit hook_config passes through when no plugins."""
    resolved = ExtensionConfig(hook_config=_make_hook_config()).resolve()
    assert resolved.hooks is not None


def test_resolve_existing_mcp_config_passthrough():
    """existing_mcp_config is the base when no plugins add MCP."""
    base_mcp = {"mcpServers": {"base-server": {"command": "test"}}}
    resolved = ExtensionConfig().resolve(existing_mcp_config=base_mcp)
    assert "base-server" in resolved.mcp_config.get("mcpServers", {})


# ---------------------------------------------------------------------------
# resolve() — with local plugins (real dirs, no git)
# ---------------------------------------------------------------------------


def test_resolve_plugin_skills_override_explicit(tmp_path):
    """Plugin skills override explicit skills with the same name (last-wins)."""
    plugin_dir = _create_plugin_dir(
        tmp_path,
        "overrider",
        skills=[{"name": "shared", "content": "from plugin"}],
    )
    explicit = _make_skill("shared", description="from config")

    resolved = ExtensionConfig(
        skills=[explicit],
        plugins=[PluginSource(source=str(plugin_dir))],
    ).resolve()

    matched = [s for s in resolved.skills if s.name == "shared"]
    assert len(matched) == 1
    # Plugin skill wins over explicit (plugins are highest precedence)
    assert "from plugin" in (matched[0].description or matched[0].content or "")


def test_resolve_plugin_mcp_merges(tmp_path):
    """Plugin MCP config merges with existing, last-wins by server name."""
    plugin_dir = _create_plugin_dir(
        tmp_path,
        "mcp-plugin",
        mcp_config={
            "mcpServers": {
                "new-server": {"command": "new-cmd"},
                "shared": {"command": "plugin-cmd"},
            }
        },
    )
    existing_mcp = {"mcpServers": {"shared": {"command": "old-cmd"}}}

    resolved = ExtensionConfig(
        plugins=[PluginSource(source=str(plugin_dir))],
    ).resolve(existing_mcp_config=existing_mcp)

    servers = resolved.mcp_config.get("mcpServers", {})
    assert "new-server" in servers
    assert servers["shared"]["command"] == "plugin-cmd"


def test_resolve_plugin_hooks_concatenate(tmp_path):
    """Plugin hooks concatenate with explicit hooks (explicit first)."""
    plugin_dir = _create_plugin_dir(
        tmp_path,
        "hook-plugin",
        hooks={
            "session_start": [
                {
                    "matcher": "*",
                    "hooks": [{"command": "echo from-plugin"}],
                }
            ]
        },
    )
    explicit_hooks = HookConfig(
        session_start=[
            HookMatcher(
                matcher="*",
                hooks=[HookDefinition(command="echo from-config")],
            )
        ]
    )

    resolved = ExtensionConfig(
        hook_config=explicit_hooks,
        plugins=[PluginSource(source=str(plugin_dir))],
    ).resolve()

    assert resolved.hooks is not None
    # Both session_start matchers should be present (concatenation)
    assert len(resolved.hooks.session_start) >= 2


def test_resolve_plugin_agents_collected(tmp_path):
    """Agent definitions from plugins are collected."""
    plugin_dir = _create_plugin_dir(
        tmp_path,
        "agent-plugin",
        agents=[{"name": "helper-agent", "content": "A helper agent."}],
    )

    resolved = ExtensionConfig(
        plugins=[PluginSource(source=str(plugin_dir))],
    ).resolve()

    assert len(resolved.agents) >= 1
    assert any(a.name == "helper-agent" for a in resolved.agents)


def test_resolve_multiple_plugins_merge_order(tmp_path):
    """Multiple plugins merge in list order (later overrides earlier)."""
    p1 = _create_plugin_dir(
        tmp_path,
        "first-plugin",
        skills=[{"name": "shared-skill", "content": "from first"}],
        mcp_config={"mcpServers": {"srv": {"command": "first"}}},
    )
    p2 = _create_plugin_dir(
        tmp_path,
        "second-plugin",
        skills=[{"name": "shared-skill", "content": "from second"}],
        mcp_config={"mcpServers": {"srv": {"command": "second"}}},
    )

    resolved = ExtensionConfig(
        plugins=[
            PluginSource(source=str(p1)),
            PluginSource(source=str(p2)),
        ],
    ).resolve()

    # Second plugin wins for same-named skill
    matched = [s for s in resolved.skills if s.name == "shared-skill"]
    assert len(matched) == 1
    assert "from second" in (matched[0].description or matched[0].content or "")

    # Second plugin wins for same-named MCP server
    assert resolved.mcp_config["mcpServers"]["srv"]["command"] == "second"


def test_resolve_resolved_plugins_populated(tmp_path):
    """resolved_plugins list is populated for each plugin spec."""
    p1 = _create_plugin_dir(tmp_path, "plugin-a")
    p2 = _create_plugin_dir(tmp_path, "plugin-b")

    resolved = ExtensionConfig(
        plugins=[
            PluginSource(source=str(p1)),
            PluginSource(source=str(p2)),
        ],
    ).resolve()

    assert len(resolved.resolved_plugins) == 2


# ---------------------------------------------------------------------------
# Full merge precedence
# ---------------------------------------------------------------------------


def test_full_merge_precedence(tmp_path):
    """Verify: existing < explicit < plugin for skills."""
    existing = _make_skill("s1", description="existing")
    explicit = _make_skill("s1", description="explicit")
    plugin_dir = _create_plugin_dir(
        tmp_path,
        "precedence-plugin",
        skills=[{"name": "s1", "content": "plugin wins"}],
    )

    resolved = ExtensionConfig(
        skills=[explicit],
        plugins=[PluginSource(source=str(plugin_dir))],
    ).resolve(existing_skills=[existing])

    matched = [s for s in resolved.skills if s.name == "s1"]
    assert len(matched) == 1
    # Plugin (highest) should win
    assert "plugin wins" in (matched[0].description or matched[0].content or "")
