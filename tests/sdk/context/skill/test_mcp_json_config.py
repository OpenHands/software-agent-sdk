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


class TestFindMcpConfig:
    """Tests for find_mcp_config() function."""

    def test_finds_mcp_json(self, tmp_path: Path) -> None:
        """Should find .mcp.json file in directory."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        mcp_json = skill_dir / ".mcp.json"
        mcp_json.write_text('{"mcpServers": {}}')

        result = find_mcp_config(skill_dir)
        assert result == mcp_json

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """Should return None when no .mcp.json exists."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Skill")

        result = find_mcp_config(skill_dir)
        assert result is None

    def test_returns_none_for_non_directory(self, tmp_path: Path) -> None:
        """Should return None for non-directory path."""
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("content")

        result = find_mcp_config(file_path)
        assert result is None


class TestExpandMcpVariables:
    """Tests for expand_mcp_variables() function."""

    def test_expands_simple_variable(self) -> None:
        """Should expand ${VAR} with provided value."""
        config = {"command": "${MY_VAR}"}
        result = expand_mcp_variables(config, {"MY_VAR": "value"})
        assert result["command"] == "value"

    def test_expands_env_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should expand ${VAR} from environment."""
        monkeypatch.setenv("TEST_ENV_VAR", "env_value")
        config = {"command": "${TEST_ENV_VAR}"}
        result = expand_mcp_variables(config, {})
        assert result["command"] == "env_value"

    def test_provided_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Provided variables should take precedence over environment."""
        monkeypatch.setenv("MY_VAR", "env_value")
        config = {"command": "${MY_VAR}"}
        result = expand_mcp_variables(config, {"MY_VAR": "provided_value"})
        assert result["command"] == "provided_value"

    def test_expands_with_default(self) -> None:
        """Should use default value when variable not found."""
        config = {"command": "${MISSING:-default_value}"}
        result = expand_mcp_variables(config, {})
        assert result["command"] == "default_value"

    def test_keeps_original_when_not_found(self) -> None:
        """Should keep original when variable not found and no default."""
        config = {"command": "${MISSING}"}
        result = expand_mcp_variables(config, {})
        assert result["command"] == "${MISSING}"

    def test_expands_nested_values(self) -> None:
        """Should expand variables in nested structures."""
        config = {
            "mcpServers": {
                "test": {
                    "command": "${CMD}",
                    "args": ["--path", "${PATH_VAR}"],
                }
            }
        }
        result = expand_mcp_variables(config, {"CMD": "python", "PATH_VAR": "/tmp"})
        assert result["mcpServers"]["test"]["command"] == "python"
        assert result["mcpServers"]["test"]["args"][1] == "/tmp"

    def test_expands_skill_root(self) -> None:
        """Should expand ${SKILL_ROOT} variable."""
        config = {"command": "${SKILL_ROOT}/scripts/run.sh"}
        result = expand_mcp_variables(config, {"SKILL_ROOT": "/path/to/skill"})
        assert result["command"] == "/path/to/skill/scripts/run.sh"


class TestLoadMcpConfig:
    """Tests for load_mcp_config() function."""

    def test_loads_valid_config(self, tmp_path: Path) -> None:
        """Should load valid .mcp.json file."""
        mcp_json = tmp_path / ".mcp.json"
        config = {
            "mcpServers": {
                "test-server": {
                    "command": "python",
                    "args": ["-m", "test_server"],
                }
            }
        }
        mcp_json.write_text(json.dumps(config))

        result = load_mcp_config(mcp_json)
        assert "mcpServers" in result
        assert "test-server" in result["mcpServers"]

    def test_expands_skill_root(self, tmp_path: Path) -> None:
        """Should expand ${SKILL_ROOT} in config."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        mcp_json = skill_dir / ".mcp.json"
        config = {
            "mcpServers": {
                "test": {
                    "command": "${SKILL_ROOT}/scripts/run.sh",
                    "args": [],
                }
            }
        }
        mcp_json.write_text(json.dumps(config))

        result = load_mcp_config(mcp_json, skill_root=skill_dir)
        assert result["mcpServers"]["test"]["command"] == f"{skill_dir}/scripts/run.sh"

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        """Should raise error for invalid JSON."""
        mcp_json = tmp_path / ".mcp.json"
        mcp_json.write_text("not valid json")

        with pytest.raises(SkillValidationError) as exc_info:
            load_mcp_config(mcp_json)
        assert "Invalid JSON" in str(exc_info.value)

    def test_raises_on_non_object(self, tmp_path: Path) -> None:
        """Should raise error when JSON is not an object."""
        mcp_json = tmp_path / ".mcp.json"
        mcp_json.write_text('["array", "not", "object"]')

        with pytest.raises(SkillValidationError) as exc_info:
            load_mcp_config(mcp_json)
        assert "expected object" in str(exc_info.value)


class TestSkillLoadWithMcpJson:
    """Tests for Skill.load() with .mcp.json support."""

    def test_loads_mcp_json_from_skill_directory(self, tmp_path: Path) -> None:
        """Should load .mcp.json when loading SKILL.md."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        # Create SKILL.md
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers:
  - test
---
# My Skill
"""
        )

        # Create .mcp.json
        mcp_config = {
            "mcpServers": {
                "test-server": {
                    "command": "python",
                    "args": ["-m", "server"],
                }
            }
        }
        (my_skill_dir / ".mcp.json").write_text(json.dumps(mcp_config))

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        assert skill.mcp_tools is not None
        assert "mcpServers" in skill.mcp_tools
        assert skill.mcp_config_path == str(my_skill_dir / ".mcp.json")

    def test_mcp_json_takes_precedence_over_frontmatter(self, tmp_path: Path) -> None:
        """Should use .mcp.json over mcp_tools frontmatter."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        # Create SKILL.md with mcp_tools in frontmatter
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers:
  - test
mcp_tools:
  mcpServers:
    frontmatter-server:
      command: old
      args: []
---
# My Skill
"""
        )

        # Create .mcp.json with different config
        mcp_config = {
            "mcpServers": {
                "file-server": {
                    "command": "new",
                    "args": [],
                }
            }
        }
        (my_skill_dir / ".mcp.json").write_text(json.dumps(mcp_config))

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        assert skill.mcp_tools is not None
        assert "file-server" in skill.mcp_tools["mcpServers"]
        assert "frontmatter-server" not in skill.mcp_tools["mcpServers"]

    def test_falls_back_to_frontmatter_without_mcp_json(self, tmp_path: Path) -> None:
        """Should use mcp_tools frontmatter when no .mcp.json exists."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        # Create SKILL.md with mcp_tools in frontmatter
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
mcp_tools:
  mcpServers:
    frontmatter-server:
      command: test
      args: []
---
# My Skill
"""
        )

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        assert skill.mcp_tools is not None
        assert "frontmatter-server" in skill.mcp_tools["mcpServers"]
        assert skill.mcp_config_path is None

    def test_no_mcp_json_for_flat_skills(self, tmp_path: Path) -> None:
        """Should not look for .mcp.json for flat .md files."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        # Create flat skill file
        skill_md = skill_dir / "flat-skill.md"
        skill_md.write_text(
            """---
triggers:
  - test
---
# Flat Skill
"""
        )

        # Create .mcp.json in skills dir (should be ignored)
        mcp_config = {"mcpServers": {"ignored": {"command": "x", "args": []}}}
        (skill_dir / ".mcp.json").write_text(json.dumps(mcp_config))

        skill = Skill.load(skill_md, skill_dir)
        assert skill.mcp_tools is None
        assert skill.mcp_config_path is None

    def test_mcp_config_path_is_set(self, tmp_path: Path) -> None:
        """Should set mcp_config_path when loading from .mcp.json."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text("# My Skill")

        mcp_json = my_skill_dir / ".mcp.json"
        mcp_json.write_text('{"mcpServers": {}}')

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        assert skill.mcp_config_path == str(mcp_json)
