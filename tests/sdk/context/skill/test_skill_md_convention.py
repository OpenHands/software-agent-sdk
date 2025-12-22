"""Tests for SKILL.md file convention and name validation (Issue #1475)."""

from pathlib import Path

import pytest

from openhands.sdk.context.skills import (
    Skill,
    SkillValidationError,
    find_skill_md,
    load_skills_from_dir,
    validate_skill_name,
)


def test_find_skill_md(tmp_path: Path) -> None:
    """find_skill_md() should locate SKILL.md files case-insensitively."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()

    # Not found
    assert find_skill_md(skill_dir) is None

    # Found (case-insensitive)
    skill_md = skill_dir / "skill.MD"
    skill_md.write_text("# My Skill")
    assert find_skill_md(skill_dir) == skill_md


def test_validate_skill_name() -> None:
    """validate_skill_name() should enforce AgentSkills naming rules."""
    # Valid names
    assert validate_skill_name("my-skill") == []
    assert validate_skill_name("skill2") == []
    assert validate_skill_name("my-cool-skill") == []

    # Invalid names
    assert len(validate_skill_name("MySkill")) == 1  # Uppercase
    assert len(validate_skill_name("my_skill")) == 1  # Underscore
    assert len(validate_skill_name("-myskill")) == 1  # Starts with hyphen
    assert len(validate_skill_name("my--skill")) == 1  # Consecutive hyphens
    assert len(validate_skill_name("a" * 65)) == 1  # Too long
    assert len(validate_skill_name("")) == 1  # Empty

    # Directory name mismatch
    errors = validate_skill_name("my-skill", directory_name="other-skill")
    assert "does not match directory" in errors[0]


def test_skill_load_with_directory_name(tmp_path: Path) -> None:
    """Skill.load() should use directory_name for SKILL.md format."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    my_skill_dir = skill_dir / "pdf-tools"
    my_skill_dir.mkdir()
    (my_skill_dir / "SKILL.md").write_text("---\ntriggers:\n  - pdf\n---\n# PDF Tools")

    # Uses directory name
    skill = Skill.load(my_skill_dir / "SKILL.md", skill_dir, directory_name="pdf-tools")
    assert skill.name == "pdf-tools"

    # Validates name when requested
    bad_dir = skill_dir / "Bad_Name"
    bad_dir.mkdir()
    (bad_dir / "SKILL.md").write_text("# Bad")
    with pytest.raises(SkillValidationError, match="Invalid skill name"):
        Skill.load(
            bad_dir / "SKILL.md",
            skill_dir,
            directory_name="Bad_Name",
            validate_name=True,
        )


def test_load_skills_from_dir_with_skill_md(tmp_path: Path) -> None:
    """load_skills_from_dir() should discover SKILL.md directories."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Flat skill
    (skills_dir / "flat-skill.md").write_text("---\ntriggers:\n  - flat\n---\n# Flat")

    # SKILL.md directory
    dir_skill = skills_dir / "dir-skill"
    dir_skill.mkdir()
    (dir_skill / "SKILL.md").write_text("---\ntriggers:\n  - dir\n---\n# Dir")

    repo_skills, knowledge_skills = load_skills_from_dir(skills_dir)
    assert "flat-skill" in knowledge_skills
    assert "dir-skill" in knowledge_skills
    assert knowledge_skills["dir-skill"].name == "dir-skill"
