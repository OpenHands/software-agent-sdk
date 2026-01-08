"""Tests for the convert_legacy_skills module."""

import json

import pytest

from openhands.sdk.context.skills.conversion import (
    convert_legacy_skill,
    convert_skills_directory,
    generate_description,
    normalize_skill_name,
    validate_skill_name,
)


class TestNormalizeSkillName:
    """Tests for normalize_skill_name function."""

    def test_lowercase_conversion(self):
        assert normalize_skill_name("GitHub") == "github"
        assert normalize_skill_name("DOCKER") == "docker"

    def test_underscore_to_hyphen(self):
        assert normalize_skill_name("add_agent") == "add-agent"
        assert normalize_skill_name("fix_test") == "fix-test"

    def test_remove_invalid_chars(self):
        assert normalize_skill_name("skill@name") == "skillname"
        assert normalize_skill_name("skill.name") == "skillname"

    def test_remove_consecutive_hyphens(self):
        assert normalize_skill_name("skill--name") == "skill-name"
        assert normalize_skill_name("a---b") == "a-b"

    def test_strip_leading_trailing_hyphens(self):
        assert normalize_skill_name("-skill-") == "skill"
        assert normalize_skill_name("--name--") == "name"


class TestValidateSkillName:
    """Tests for validate_skill_name function."""

    def test_valid_names(self):
        assert validate_skill_name("github") == []
        assert validate_skill_name("pdf-tools") == []
        assert validate_skill_name("my-skill-123") == []

    def test_empty_name(self):
        errors = validate_skill_name("")
        assert len(errors) == 1
        assert "empty" in errors[0].lower()

    def test_too_long_name(self):
        long_name = "a" * 65
        errors = validate_skill_name(long_name)
        assert any("64" in e for e in errors)

    def test_invalid_characters(self):
        errors = validate_skill_name("GitHub")
        assert len(errors) > 0
        assert any("lowercase" in e.lower() for e in errors)


class TestGenerateDescription:
    """Tests for generate_description function."""

    def test_extract_from_content(self):
        content = "# Header\n\nThis is the first paragraph."
        desc = generate_description(content)
        assert desc == "This is the first paragraph."

    def test_skip_headers(self):
        content = "# Header\n## Subheader\nActual content here."
        desc = generate_description(content)
        assert desc == "Actual content here."

    def test_fallback_to_triggers(self):
        content = "# Only Headers\n## No Content"
        desc = generate_description(content, triggers=["git", "github"])
        assert "git" in desc
        assert "github" in desc

    def test_fallback_to_name(self):
        content = "# Only Headers"
        desc = generate_description(content, triggers=[], name="my-skill")
        assert "my-skill" in desc

    def test_truncate_long_description(self):
        content = "A" * 2000
        desc = generate_description(content)
        assert len(desc) <= 1024


@pytest.fixture
def legacy_skill_file(tmp_path):
    """Create a legacy skill file for testing."""
    skill_file = tmp_path / "test-skill.md"
    skill_file.write_text(
        "---\n"
        "name: test_skill\n"
        "type: knowledge\n"
        "version: 1.0.0\n"
        "agent: CodeActAgent\n"
        "triggers:\n"
        "  - test\n"
        "  - testing\n"
        "---\n"
        "# Test Skill\n\n"
        "This is a test skill for unit testing."
    )
    return skill_file


@pytest.fixture
def legacy_skill_with_mcp(tmp_path):
    """Create a legacy skill file with mcp_tools."""
    skill_file = tmp_path / "mcp-skill.md"
    skill_file.write_text(
        "---\n"
        "name: mcp_skill\n"
        "triggers:\n"
        "  - mcp\n"
        "mcp_tools:\n"
        "  mcpServers:\n"
        "    test-server:\n"
        "      command: python\n"
        "      args:\n"
        "        - -m\n"
        "        - test_server\n"
        "---\n"
        "# MCP Skill\n\n"
        "This skill has MCP tools."
    )
    return skill_file


@pytest.fixture
def legacy_skill_with_inputs(tmp_path):
    """Create a legacy skill file with inputs (task skill)."""
    skill_file = tmp_path / "task-skill.md"
    skill_file.write_text(
        "---\n"
        "name: task_skill\n"
        "triggers:\n"
        "  - /task\n"
        "inputs:\n"
        "  - name: INPUT_VAR\n"
        "    description: An input variable\n"
        "---\n"
        "# Task Skill\n\n"
        "This is a task skill with inputs."
    )
    return skill_file


def test_convert_legacy_skill_basic(legacy_skill_file, tmp_path):
    """Test basic conversion of a legacy skill."""
    output_dir = tmp_path / "output"
    result = convert_legacy_skill(legacy_skill_file, output_dir)

    assert result is not None
    assert result.name == "test-skill"
    assert result.is_dir()

    skill_md = result / "SKILL.md"
    assert skill_md.exists()

    content = skill_md.read_text()
    assert "name: test-skill" in content
    assert "description:" in content
    assert "triggers:" in content
    assert "- test" in content
    assert "- testing" in content
    assert "metadata:" in content


def test_convert_legacy_skill_with_mcp(legacy_skill_with_mcp, tmp_path):
    """Test conversion of a skill with mcp_tools to .mcp.json."""
    output_dir = tmp_path / "output"
    result = convert_legacy_skill(legacy_skill_with_mcp, output_dir)

    assert result is not None
    mcp_json = result / ".mcp.json"
    assert mcp_json.exists()

    mcp_config = json.loads(mcp_json.read_text())
    assert "mcpServers" in mcp_config
    assert "test-server" in mcp_config["mcpServers"]


def test_convert_legacy_skill_with_inputs(legacy_skill_with_inputs, tmp_path):
    """Test conversion preserves inputs for task skills."""
    output_dir = tmp_path / "output"
    result = convert_legacy_skill(legacy_skill_with_inputs, output_dir)

    assert result is not None
    skill_md = result / "SKILL.md"
    content = skill_md.read_text()

    assert "inputs:" in content
    assert "INPUT_VAR" in content


def test_convert_legacy_skill_dry_run(legacy_skill_file, tmp_path):
    """Test dry run doesn't create files."""
    output_dir = tmp_path / "output"
    result = convert_legacy_skill(legacy_skill_file, output_dir, dry_run=True)

    assert result is not None
    assert not result.exists()


def test_convert_legacy_skill_skip_readme(tmp_path):
    """Test that README.md is skipped."""
    readme = tmp_path / "README.md"
    readme.write_text("# README\n\nThis is a readme.")

    output_dir = tmp_path / "output"
    result = convert_legacy_skill(readme, output_dir)

    assert result is None


@pytest.fixture
def skills_directory(tmp_path):
    """Create a directory with multiple legacy skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Create multiple skill files
    (skills_dir / "skill1.md").write_text(
        "---\nname: skill1\ntriggers:\n  - s1\n---\nSkill 1 content."
    )
    (skills_dir / "skill2.md").write_text(
        "---\nname: skill2\ntriggers:\n  - s2\n---\nSkill 2 content."
    )
    (skills_dir / "README.md").write_text("# Skills\n\nThis is a readme.")

    return skills_dir


def test_convert_skills_directory(skills_directory, tmp_path):
    """Test converting a directory of skills."""
    output_dir = tmp_path / "output"
    results = convert_skills_directory(skills_directory, output_dir)

    assert len(results) == 2
    assert (output_dir / "skill1" / "SKILL.md").exists()
    assert (output_dir / "skill2" / "SKILL.md").exists()


def test_convert_skills_directory_dry_run(skills_directory, tmp_path):
    """Test dry run on directory."""
    output_dir = tmp_path / "output"
    results = convert_skills_directory(skills_directory, output_dir, dry_run=True)

    assert len(results) == 2
    assert not output_dir.exists()


def test_converted_skills_loadable_by_skill_class(legacy_skill_file, tmp_path):
    """Test that converted skills can be loaded by the Skill class."""
    from openhands.sdk.context.skills import Skill

    output_dir = tmp_path / "output"
    result = convert_legacy_skill(legacy_skill_file, output_dir)

    assert result is not None
    skill_md = result / "SKILL.md"

    # Load the converted skill using Skill.load
    skill = Skill.load(skill_md)

    assert skill.name == "test-skill"
    assert skill.description is not None
    assert "test" in skill.description.lower() or "skill" in skill.description.lower()
    assert skill.trigger is not None  # Should have KeywordTrigger from triggers


def test_converted_skills_with_mcp_loadable(legacy_skill_with_mcp, tmp_path):
    """Test that converted skills with MCP config can be loaded."""
    from openhands.sdk.context.skills import Skill

    output_dir = tmp_path / "output"
    result = convert_legacy_skill(legacy_skill_with_mcp, output_dir)

    assert result is not None
    skill_md = result / "SKILL.md"

    # Load the converted skill
    skill = Skill.load(skill_md)

    assert skill.name == "mcp-skill"
    assert skill.mcp_tools is not None
    assert "mcpServers" in skill.mcp_tools
    assert "test-server" in skill.mcp_tools["mcpServers"]


def test_load_public_skills_with_converted_format(tmp_path):
    """Test that load_public_skills works with converted AgentSkills format.

    This test simulates a repository that has been converted to the AgentSkills
    format (SKILL.md directories) and verifies that load_public_skills can
    still load them correctly.
    """
    from unittest.mock import patch

    from openhands.sdk.context.skills import KeywordTrigger, load_public_skills

    # Create a mock repository with converted skills (AgentSkills format)
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()
    skills_dir = repo_dir / "skills"
    skills_dir.mkdir()

    # Create AgentSkills-format skill directories
    git_skill_dir = skills_dir / "git"
    git_skill_dir.mkdir()
    (git_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: git\n"
        "description: Git best practices and commands.\n"
        "triggers:\n"
        "  - git\n"
        "  - github\n"
        "metadata:\n"
        "  version: '1.0.0'\n"
        "---\n"
        "Git best practices and commands."
    )

    docker_skill_dir = skills_dir / "docker"
    docker_skill_dir.mkdir()
    (docker_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: docker\n"
        "description: Docker guidelines and commands.\n"
        "triggers:\n"
        "  - docker\n"
        "  - container\n"
        "---\n"
        "Docker guidelines and commands."
    )

    # Create a skill with .mcp.json
    mcp_skill_dir = skills_dir / "mcp-skill"
    mcp_skill_dir.mkdir()
    (mcp_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: mcp-skill\n"
        "description: A skill with MCP tools.\n"
        "triggers:\n"
        "  - mcp\n"
        "---\n"
        "This skill has MCP tools."
    )
    (mcp_skill_dir / ".mcp.json").write_text(
        '{"mcpServers": {"test-server": {"command": "python"}}}'
    )

    # Create .git directory to simulate a git repo
    (repo_dir / ".git").mkdir()

    def mock_update_repo(repo_url, branch, cache_dir):
        return repo_dir

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        skills = load_public_skills()

        # Should load all 3 skills
        assert len(skills) == 3
        skill_names = {s.name for s in skills}
        assert skill_names == {"git", "docker", "mcp-skill"}

        # Check git skill details
        git_skill = next(s for s in skills if s.name == "git")
        assert isinstance(git_skill.trigger, KeywordTrigger)
        assert "git" in git_skill.trigger.keywords
        assert git_skill.description == "Git best practices and commands."

        # Check MCP skill has mcp_tools loaded from .mcp.json
        mcp_skill = next(s for s in skills if s.name == "mcp-skill")
        assert mcp_skill.mcp_tools is not None
        assert "mcpServers" in mcp_skill.mcp_tools
