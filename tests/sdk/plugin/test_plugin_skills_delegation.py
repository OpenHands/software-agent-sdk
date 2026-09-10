"""Tests for plugin skills discovery delegating to ``load_skills_from_dir``."""

from pathlib import Path

import pytest

from openhands.sdk.hooks import HookConfig
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.plugin.format.base import PluginFormat
from openhands.sdk.plugin.types import CommandDefinition, PluginManifest
from openhands.sdk.skills.exceptions import SkillValidationError
from openhands.sdk.skills.skill import Skill, load_skills_from_dir
from openhands.sdk.subagent.schema import AgentDefinition


class _StubFormat(PluginFormat):
    """Minimal concrete PluginFormat exposing only the shared load_skills."""

    name = "stub-format"

    @classmethod
    def detect(cls, plugin_dir: Path) -> bool:
        return False

    def load_manifest(self, plugin_dir: Path) -> PluginManifest:
        raise NotImplementedError

    def load_mcp_config(self, plugin_dir: Path) -> dict[str, MCPServer]:
        raise NotImplementedError

    def load_hooks(self, plugin_dir: Path) -> HookConfig | None:
        raise NotImplementedError

    def load_agents(self, plugin_dir: Path) -> list[AgentDefinition]:
        raise NotImplementedError

    def load_commands(self, plugin_dir: Path) -> list[CommandDefinition]:
        raise NotImplementedError


def test_plugin_skills_nested_md_not_loaded_as_skills(tmp_path: Path):
    """Nested .md files under a skill dir must not become skills."""
    skill_dir = tmp_path / "skills" / "pdf-tools"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: pdf-tools\n---\n# PDF Tools")
    (skill_dir / "references" / "notes.md").write_text("# Notes")

    # A loose .md under a subdirectory WITHOUT SKILL.md is the real recursion
    # hazard: a recursive scan would load it as a skill, the plugin contract
    # forbids it ("do not recursively search deeper descendants").
    shared = tmp_path / "skills" / "shared"
    shared.mkdir(parents=True)
    (shared / "notes.md").write_text("# Shared notes")

    skills = _StubFormat().load_skills(tmp_path)

    assert [s.name for s in skills] == ["pdf-tools"]


def test_plugin_skills_relaxed_name_validation(tmp_path: Path):
    """Plugin loading keeps strict=False so non-conforming names still load."""
    skill_dir = tmp_path / "skills" / "Bad_Name"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: Bad_Name\n---\n# Bad")

    skills = _StubFormat().load_skills(tmp_path)

    assert [s.name for s in skills] == ["Bad_Name"]
    # Contrast: strict loading rejects the same skill.
    with pytest.raises(SkillValidationError):
        Skill.load(skill_dir / "SKILL.md", tmp_path / "skills", strict=True)
    repo, knowledge, agent = load_skills_from_dir(tmp_path / "skills")
    assert "Bad_Name" not in {**repo, **knowledge, **agent}


def test_plugin_skills_one_bad_skill_skips_only_itself(tmp_path: Path):
    """A skill raising outside the old narrow catch set skips only itself."""
    skills_dir = tmp_path / "skills"
    good = skills_dir / "good"
    broken = skills_dir / "broken"
    good.mkdir(parents=True)
    broken.mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: good\n---\n# Good")
    (broken / "SKILL.md").write_bytes(b"---\nname: broken\n---\n# \xff\xfe")

    skills = _StubFormat().load_skills(tmp_path)

    assert [s.name for s in skills] == ["good"]


def test_plugin_skills_loose_md_at_root(tmp_path: Path):
    """A loose .md file directly under skills/ loads as a skill."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "loose-skill.md").write_text("# Loose")

    skills = _StubFormat().load_skills(tmp_path)

    assert [s.name for s in skills] == ["loose-skill"]


def test_plugin_skills_empty_dir(tmp_path: Path):
    """An empty skills/ dir yields no skills; root SKILL.md still loads."""
    (tmp_path / "skills").mkdir(parents=True)
    assert _StubFormat().load_skills(tmp_path) == []

    plugin_root = tmp_path / "single"
    plugin_root.mkdir(parents=True)
    (plugin_root / "SKILL.md").write_text("---\nname: root-skill\n---\n# Root")

    skills = _StubFormat().load_skills(plugin_root)

    assert [s.name for s in skills] == ["root-skill"]
