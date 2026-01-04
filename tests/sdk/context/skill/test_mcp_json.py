"""Tests for .mcp.json support in AgentSkills (Issue #1476).

Key behaviors tested:
1. AgentSkills (SKILL.md) load .mcp.json when present
2. AgentSkills ignore mcp_tools frontmatter (only use .mcp.json)
3. Legacy skills load mcp_tools from frontmatter
4. Legacy skills don't load .mcp.json
5. Variable expansion works (${VAR}, ${VAR:-default}, ${SKILL_ROOT})
"""

import json
from pathlib import Path

import pytest

from openhands.sdk.context.skills import (
    Skill,
    SkillValidationError,
    load_skills_from_dir,
)


def test_agentskills_loads_mcp_json(tmp_path: Path) -> None:
    """AgentSkills (SKILL.md) should load .mcp.json with variable expansion."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill")
    mcp_config = {
        "mcpServers": {
            "server": {
                "command": "${SKILL_ROOT}/run.py",
                "args": ["--port", "${PORT:-8080}"],
            }
        }
    }
    (skill_dir / ".mcp.json").write_text(json.dumps(mcp_config))

    skill = Skill.load(skill_dir / "SKILL.md")

    assert skill.mcp_tools is not None
    # ${SKILL_ROOT} should be expanded
    assert skill.mcp_tools["mcpServers"]["server"]["command"] == f"{skill_dir}/run.py"
    # ${PORT:-8080} should use default
    assert skill.mcp_tools["mcpServers"]["server"]["args"] == ["--port", "8080"]


def test_agentskills_ignores_frontmatter_mcp_tools(tmp_path: Path) -> None:
    """AgentSkills should ONLY use .mcp.json, ignoring mcp_tools frontmatter."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    # Frontmatter has mcp_tools but no .mcp.json file
    (skill_dir / "SKILL.md").write_text(
        "---\nmcp_tools:\n  mcpServers:\n    server: {command: python}\n---\n# Skill"
    )

    skill = Skill.load(skill_dir / "SKILL.md")
    assert skill.mcp_tools is None


def test_legacy_skill_loads_frontmatter_mcp_tools(tmp_path: Path) -> None:
    """Legacy skills (.md files) should load mcp_tools from frontmatter."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "legacy.md").write_text(
        "---\nmcp_tools:\n  mcpServers:\n    server: {command: python}\n---\n# Legacy"
    )

    skill = Skill.load(skills_dir / "legacy.md", skills_dir)

    assert skill.mcp_tools is not None
    assert "server" in skill.mcp_tools["mcpServers"]


def test_legacy_skill_ignores_mcp_json_in_directory(tmp_path: Path) -> None:
    """Legacy skills should NOT load .mcp.json even if present in directory."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "legacy.md").write_text("# Legacy Skill")
    (skills_dir / ".mcp.json").write_text(
        '{"mcpServers": {"server": {"command": "python", "args": []}}}'
    )

    skill = Skill.load(skills_dir / "legacy.md", skills_dir)
    assert skill.mcp_tools is None


def test_mcp_json_invalid_json_raises_error(tmp_path: Path) -> None:
    """Invalid JSON in .mcp.json should raise SkillValidationError."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill")
    (skill_dir / ".mcp.json").write_text("not valid json")

    with pytest.raises(SkillValidationError, match="Invalid JSON"):
        Skill.load(skill_dir / "SKILL.md")


def test_load_skills_from_dir_mcp_json_only_for_agentskills(tmp_path: Path) -> None:
    """load_skills_from_dir() should only load .mcp.json for agent_skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # AgentSkill with .mcp.json
    agent_dir = skills_dir / "agent-skill"
    agent_dir.mkdir()
    (agent_dir / "SKILL.md").write_text("# Agent Skill")
    (agent_dir / ".mcp.json").write_text(
        '{"mcpServers": {"server": {"command": "python", "args": []}}}'
    )

    # Legacy skill
    (skills_dir / "legacy.md").write_text("# Legacy Skill")

    repo_skills, _, agent_skills = load_skills_from_dir(skills_dir)

    assert agent_skills["agent-skill"].mcp_tools is not None
    assert repo_skills["legacy"].mcp_tools is None
