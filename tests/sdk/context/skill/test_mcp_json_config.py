"""Tests for .mcp.json configuration support (Issue #1476)."""

import json
from pathlib import Path

import pytest

from openhands.sdk.context.skills import (
    Skill,
    SkillValidationError,
    expand_mcp_variables,
    find_mcp_config,
    load_mcp_config,
)


def test_find_mcp_config(tmp_path: Path) -> None:
    """find_mcp_config() should locate .mcp.json files."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()

    # Not found
    assert find_mcp_config(skill_dir) is None

    # Found
    mcp_json = skill_dir / ".mcp.json"
    mcp_json.write_text('{"mcpServers": {}}')
    assert find_mcp_config(skill_dir) == mcp_json


def test_expand_mcp_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """expand_mcp_variables() should expand ${VAR} patterns."""
    # Simple expansion
    result = expand_mcp_variables({"cmd": "${MY_VAR}"}, {"MY_VAR": "value"})
    assert result["cmd"] == "value"

    # Environment variable
    monkeypatch.setenv("TEST_VAR", "env_value")
    result = expand_mcp_variables({"cmd": "${TEST_VAR}"}, {})
    assert result["cmd"] == "env_value"

    # Default value
    result = expand_mcp_variables({"cmd": "${MISSING:-default}"}, {})
    assert result["cmd"] == "default"

    # Nested structures
    config = {"mcpServers": {"test": {"command": "${CMD}", "args": ["${ARG}"]}}}
    result = expand_mcp_variables(config, {"CMD": "python", "ARG": "--help"})
    assert result["mcpServers"]["test"]["command"] == "python"
    assert result["mcpServers"]["test"]["args"][0] == "--help"


def test_load_mcp_config(tmp_path: Path) -> None:
    """load_mcp_config() should load and validate .mcp.json files."""
    mcp_json = tmp_path / ".mcp.json"

    # Valid config
    config = {"mcpServers": {"test": {"command": "python", "args": []}}}
    mcp_json.write_text(json.dumps(config))
    result = load_mcp_config(mcp_json)
    assert "test" in result["mcpServers"]

    # Invalid JSON
    mcp_json.write_text("not valid json")
    with pytest.raises(SkillValidationError, match="Invalid JSON"):
        load_mcp_config(mcp_json)


def test_skill_load_with_mcp_json(tmp_path: Path) -> None:
    """Skill.load() should load .mcp.json for SKILL.md directories."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    my_skill_dir = skill_dir / "my-skill"
    my_skill_dir.mkdir()

    (my_skill_dir / "SKILL.md").write_text("# My Skill")
    mcp_config = {"mcpServers": {"test": {"command": "python", "args": []}}}
    (my_skill_dir / ".mcp.json").write_text(json.dumps(mcp_config))

    # SKILL.md files auto-detect directory name
    skill = Skill.load(my_skill_dir / "SKILL.md", skill_dir)
    assert skill.mcp_tools is not None
    assert "test" in skill.mcp_tools["mcpServers"]
    assert skill.mcp_config_path == str(my_skill_dir / ".mcp.json")

    # Flat files should not load .mcp.json
    flat_skill = skill_dir / "flat.md"
    flat_skill.write_text("# Flat")
    skill = Skill.load(flat_skill, skill_dir)
    assert skill.mcp_config_path is None
