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


def test_validate_skill_name_valid() -> None:
    """validate_skill_name() should accept valid AgentSkills names."""
    assert validate_skill_name("my-skill") == []
    assert validate_skill_name("skill2") == []
    assert validate_skill_name("my-cool-skill") == []
    assert validate_skill_name("a") == []
    assert validate_skill_name("a" * 64) == []


def test_validate_skill_name_invalid_format() -> None:
    """validate_skill_name() should reject invalid name formats."""
    # Uppercase - should contain format error
    errors = validate_skill_name("MySkill")
    assert any("lowercase" in e for e in errors)

    # Underscore - should contain format error
    errors = validate_skill_name("my_skill")
    assert any("lowercase" in e for e in errors)

    # Starts with hyphen - should contain format error
    errors = validate_skill_name("-myskill")
    assert any("lowercase" in e for e in errors)

    # Consecutive hyphens - should contain format error
    errors = validate_skill_name("my--skill")
    assert any("lowercase" in e for e in errors)


def test_validate_skill_name_length() -> None:
    """validate_skill_name() should enforce length limits."""
    # Too long - should contain length error
    errors = validate_skill_name("a" * 65)
    assert any("64 characters" in e for e in errors)

    # Empty - should contain empty error
    errors = validate_skill_name("")
    assert any("empty" in e.lower() for e in errors)


def test_validate_skill_name_directory_mismatch() -> None:
    """validate_skill_name() should detect directory name mismatch."""
    errors = validate_skill_name("my-skill", directory_name="other-skill")
    assert any("does not match directory" in e for e in errors)


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


def test_skill_load_auto_validates_with_directory_name(tmp_path: Path) -> None:
    """Skill.load() should auto-validate when directory_name is provided."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()

    # Invalid directory name should raise validation error automatically
    bad_dir = skill_dir / "Bad_Name"
    bad_dir.mkdir()
    (bad_dir / "SKILL.md").write_text("# Bad")
    with pytest.raises(SkillValidationError, match="Invalid skill name"):
        Skill.load(bad_dir / "SKILL.md", skill_dir, directory_name="Bad_Name")


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
