"""Tests for extension source functions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openhands.sdk.extensions.extensions import Extensions
from openhands.sdk.extensions.sources import (
    from_inline,
    from_marketplace,
    from_plugin,
    from_project,
    from_public,
    from_user,
)
from openhands.sdk.hooks.config import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.plugin.types import PluginManifest
from openhands.sdk.skills.skill import Skill
from openhands.sdk.subagent.schema import AgentDefinition


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def simple_plugin(tmp_path: Path) -> Plugin:
    """Create a minimal Plugin with skills, hooks, MCP, and agents."""
    skill = Skill(name="plugin-skill", content="plugin skill content")
    hooks = HookConfig(
        pre_tool_use=[
            HookMatcher(matcher="*", hooks=[HookDefinition(command="echo hook")])
        ]
    )
    mcp = {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-fetch"]}}}
    agent = AgentDefinition(name="plugin-agent", description="from plugin")

    return Plugin(
        manifest=PluginManifest(name="test-plugin", version="1.0.0"),
        path=str(tmp_path),
        skills=[skill],
        hooks=hooks,
        mcp_config=mcp,
        agents=[agent],
    )


def _write_hooks_json(directory: Path, hooks_data: dict) -> Path:
    hooks_dir = directory / ".openhands"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    path = hooks_dir / "hooks.json"
    path.write_text(json.dumps(hooks_data))
    return path


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a project directory with skills and hooks."""
    skills_dir = tmp_path / ".openhands" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "my-skill.md").write_text(
        "---\ntrigger: keyword\n---\nSkill content here"
    )
    _write_hooks_json(
        tmp_path,
        {
            "pre_tool_use": [
                {"matcher": "*", "hooks": [{"command": "echo project-hook"}]}
            ]
        },
    )
    return tmp_path


# ------------------------------------------------------------------
# from_plugin
# ------------------------------------------------------------------


def test_from_plugin_extracts_skills(simple_plugin: Plugin):
    ext = from_plugin(simple_plugin)
    assert len(ext.skills) == 1
    assert ext.skills[0].name == "plugin-skill"


def test_from_plugin_extracts_hooks(simple_plugin: Plugin):
    ext = from_plugin(simple_plugin)
    assert ext.hooks is not None
    assert len(ext.hooks.pre_tool_use) == 1


def test_from_plugin_extracts_mcp(simple_plugin: Plugin):
    ext = from_plugin(simple_plugin)
    assert "fetch" in ext.mcp_config.get("mcpServers", {})


def test_from_plugin_extracts_agents(simple_plugin: Plugin):
    ext = from_plugin(simple_plugin)
    assert len(ext.agents) == 1
    assert ext.agents[0].name == "plugin-agent"


def test_from_plugin_no_hooks():
    plugin = Plugin(
        manifest=PluginManifest(name="bare"),
        path="/tmp/bare",
        skills=[],
        hooks=None,
        mcp_config=None,
    )
    ext = from_plugin(plugin)
    assert ext.hooks is None
    assert ext.mcp_config == {}


def test_from_plugin_includes_command_derived_skills(tmp_path: Path):
    """Commands are flattened into skills via Plugin.get_all_skills()."""
    from openhands.sdk.plugin.types import CommandDefinition

    cmd = CommandDefinition(
        name="review",
        description="Run code review",
        content="Review the PR",
    )
    plugin = Plugin(
        manifest=PluginManifest(name="cmd-plugin"),
        path=str(tmp_path),
        skills=[],
        commands=[cmd],
    )
    ext = from_plugin(plugin)
    assert any("review" in s.name for s in ext.skills)


# ------------------------------------------------------------------
# from_project
# ------------------------------------------------------------------


def test_from_project_loads_skills(project_dir: Path):
    ext = from_project(project_dir)
    assert len(ext.skills) > 0


def test_from_project_loads_hooks(project_dir: Path):
    ext = from_project(project_dir)
    assert ext.hooks is not None
    assert len(ext.hooks.pre_tool_use) == 1


def test_from_project_empty_dir(tmp_path: Path):
    ext = from_project(tmp_path)
    assert ext.skills == []
    assert ext.hooks is None


def test_from_project_no_hooks(tmp_path: Path):
    """Project with skills but no hooks.json."""
    skills_dir = tmp_path / ".openhands" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "test.md").write_text("---\ntrigger: keyword\n---\nContent")

    ext = from_project(tmp_path)
    assert len(ext.skills) > 0
    assert ext.hooks is None


def test_from_project_invalid_hooks_json(tmp_path: Path):
    """Invalid hooks.json should not crash, just return None hooks."""
    hooks_dir = tmp_path / ".openhands"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text("not valid json{{{")

    ext = from_project(tmp_path)
    assert ext.hooks is None


def test_from_project_custom_hooks_path(tmp_path: Path):
    """hooks_path overrides the default location."""
    custom_hooks = tmp_path / "custom" / "my-hooks.json"
    custom_hooks.parent.mkdir(parents=True)
    custom_hooks.write_text(
        json.dumps({"stop": [{"matcher": "*", "hooks": [{"command": "echo stop"}]}]})
    )

    ext = from_project(tmp_path, hooks_path=custom_hooks)
    assert ext.hooks is not None
    assert len(ext.hooks.stop) == 1


# ------------------------------------------------------------------
# from_user
# ------------------------------------------------------------------


def test_from_user_returns_extensions(monkeypatch: pytest.MonkeyPatch):
    """from_user() returns an Extensions even when nothing is found."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("HOME", td)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path(td)))
        ext = from_user()
        assert isinstance(ext, Extensions)


def test_from_user_custom_hooks_path(tmp_path: Path):
    """hooks_path overrides the default user hooks location."""
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {"session_start": [{"matcher": "*", "hooks": [{"command": "echo hi"}]}]}
        )
    )

    ext = from_user(hooks_path=hooks_file)
    assert ext.hooks is not None
    assert len(ext.hooks.session_start) == 1


# ------------------------------------------------------------------
# from_marketplace / from_public
# ------------------------------------------------------------------


def test_from_marketplace_returns_extensions(monkeypatch: pytest.MonkeyPatch):
    """from_marketplace() returns Extensions even if loading fails."""
    monkeypatch.setattr(
        "openhands.sdk.extensions.sources.load_public_skills",
        lambda **kwargs: [],
    )
    ext = from_marketplace()
    assert isinstance(ext, Extensions)
    assert ext.skills == []


def test_from_marketplace_forwards_params(monkeypatch: pytest.MonkeyPatch):
    """All parameters are forwarded to load_public_skills."""
    captured: dict = {}

    def mock_load(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "openhands.sdk.extensions.sources.load_public_skills",
        mock_load,
    )
    from_marketplace(
        repo_url="https://example.com/repo",
        branch="dev",
        marketplace_path="custom/marketplace.json",
    )
    assert captured["repo_url"] == "https://example.com/repo"
    assert captured["branch"] == "dev"
    assert captured["marketplace_path"] == "custom/marketplace.json"


def test_from_marketplace_handles_exception(monkeypatch: pytest.MonkeyPatch):
    """from_marketplace() doesn't crash on loader exceptions."""

    def failing_load(**kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(
        "openhands.sdk.extensions.sources.load_public_skills",
        failing_load,
    )
    ext = from_marketplace()
    assert ext.skills == []


def test_from_public_delegates_to_from_marketplace(
    monkeypatch: pytest.MonkeyPatch,
):
    """from_public() calls from_marketplace() with default args."""
    monkeypatch.setattr(
        "openhands.sdk.extensions.sources.load_public_skills",
        lambda **kwargs: [],
    )
    ext = from_public()
    assert isinstance(ext, Extensions)


# ------------------------------------------------------------------
# from_inline
# ------------------------------------------------------------------


def test_from_inline_empty():
    ext = from_inline()
    assert ext.is_empty()


def test_from_inline_with_skills():
    skill = Skill(name="s", content="c")
    ext = from_inline(skills=[skill])
    assert len(ext.skills) == 1
    assert ext.skills[0].name == "s"


def test_from_inline_with_hooks():
    hooks = HookConfig(
        pre_tool_use=[HookMatcher(hooks=[HookDefinition(command="echo hi")])]
    )
    ext = from_inline(hooks=hooks)
    assert ext.hooks is not None


def test_from_inline_with_mcp():
    ext = from_inline(mcp_config={"mcpServers": {"s": {}}})
    assert "s" in ext.mcp_config["mcpServers"]


def test_from_inline_with_agents():
    agent = AgentDefinition(name="a")
    ext = from_inline(agents=[agent])
    assert len(ext.agents) == 1


def test_from_inline_empty_hooks_becomes_none():
    """An empty HookConfig should be normalized to None."""
    ext = from_inline(hooks=HookConfig())
    assert ext.hooks is None
