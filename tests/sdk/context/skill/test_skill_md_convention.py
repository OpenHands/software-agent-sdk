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


class TestFindSkillMd:
    """Tests for find_skill_md() function."""

    def test_finds_skill_md(self, tmp_path: Path) -> None:
        """Should find SKILL.md file in directory."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# My Skill")

        result = find_skill_md(skill_dir)
        assert result == skill_md

    def test_finds_skill_md_case_insensitive(self, tmp_path: Path) -> None:
        """Should find skill.md with different casing."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "skill.MD"
        skill_md.write_text("# My Skill")

        result = find_skill_md(skill_dir)
        assert result == skill_md

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """Should return None when no SKILL.md exists."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "README.md").write_text("# README")

        result = find_skill_md(skill_dir)
        assert result is None

    def test_returns_none_for_non_directory(self, tmp_path: Path) -> None:
        """Should return None for non-directory path."""
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("content")

        result = find_skill_md(file_path)
        assert result is None


class TestValidateSkillName:
    """Tests for validate_skill_name() function."""

    def test_valid_simple_name(self) -> None:
        """Should accept simple lowercase name."""
        errors = validate_skill_name("myskill")
        assert errors == []

    def test_valid_hyphenated_name(self) -> None:
        """Should accept hyphenated name."""
        errors = validate_skill_name("my-skill")
        assert errors == []

    def test_valid_multi_hyphen_name(self) -> None:
        """Should accept name with multiple hyphens."""
        errors = validate_skill_name("my-cool-skill")
        assert errors == []

    def test_valid_with_numbers(self) -> None:
        """Should accept name with numbers."""
        errors = validate_skill_name("skill2")
        assert errors == []

    def test_invalid_uppercase(self) -> None:
        """Should reject uppercase letters."""
        errors = validate_skill_name("MySkill")
        assert len(errors) == 1
        assert "lowercase" in errors[0]

    def test_invalid_underscore(self) -> None:
        """Should reject underscores."""
        errors = validate_skill_name("my_skill")
        assert len(errors) == 1
        assert "lowercase" in errors[0]

    def test_invalid_starts_with_hyphen(self) -> None:
        """Should reject name starting with hyphen."""
        errors = validate_skill_name("-myskill")
        assert len(errors) == 1

    def test_invalid_ends_with_hyphen(self) -> None:
        """Should reject name ending with hyphen."""
        errors = validate_skill_name("myskill-")
        assert len(errors) == 1

    def test_invalid_consecutive_hyphens(self) -> None:
        """Should reject consecutive hyphens."""
        errors = validate_skill_name("my--skill")
        assert len(errors) == 1

    def test_invalid_too_long(self) -> None:
        """Should reject name exceeding 64 characters."""
        long_name = "a" * 65
        errors = validate_skill_name(long_name)
        assert len(errors) == 1
        assert "64 characters" in errors[0]

    def test_invalid_empty(self) -> None:
        """Should reject empty name."""
        errors = validate_skill_name("")
        assert len(errors) == 1
        assert "empty" in errors[0]

    def test_directory_name_match(self) -> None:
        """Should accept when name matches directory."""
        errors = validate_skill_name("my-skill", directory_name="my-skill")
        assert errors == []

    def test_directory_name_mismatch(self) -> None:
        """Should reject when name doesn't match directory."""
        errors = validate_skill_name("my-skill", directory_name="other-skill")
        assert len(errors) == 1
        assert "does not match directory" in errors[0]


class TestSkillLoadWithDirectoryName:
    """Tests for Skill.load() with directory_name parameter."""

    def test_load_skill_md_derives_name_from_directory(self, tmp_path: Path) -> None:
        """Should derive skill name from directory when loading SKILL.md."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "pdf-tools"
        my_skill_dir.mkdir()
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers:
  - pdf
---
# PDF Tools

Process PDF files.
"""
        )

        skill = Skill.load(skill_md, skill_dir, directory_name="pdf-tools")
        assert skill.name == "pdf-tools"

    def test_load_skill_md_frontmatter_name_overrides(self, tmp_path: Path) -> None:
        """Frontmatter name should override directory name."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "pdf-tools"
        my_skill_dir.mkdir()
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: custom-name
triggers:
  - pdf
---
# PDF Tools
"""
        )

        skill = Skill.load(skill_md, skill_dir, directory_name="pdf-tools")
        assert skill.name == "custom-name"

    def test_load_with_name_validation_valid(self, tmp_path: Path) -> None:
        """Should pass validation for valid name."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "pdf-tools"
        my_skill_dir.mkdir()
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers:
  - pdf
---
# PDF Tools
"""
        )

        skill = Skill.load(
            skill_md,
            skill_dir,
            directory_name="pdf-tools",
            validate_name=True,
        )
        assert skill.name == "pdf-tools"

    def test_load_with_name_validation_invalid(self, tmp_path: Path) -> None:
        """Should raise error for invalid name when validation enabled."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "PDF_Tools"
        my_skill_dir.mkdir()
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers:
  - pdf
---
# PDF Tools
"""
        )

        with pytest.raises(SkillValidationError) as exc_info:
            Skill.load(
                skill_md,
                skill_dir,
                directory_name="PDF_Tools",
                validate_name=True,
            )
        assert "Invalid skill name" in str(exc_info.value)

    def test_load_with_name_mismatch_validation(self, tmp_path: Path) -> None:
        """Should raise error when frontmatter name doesn't match directory."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "pdf-tools"
        my_skill_dir.mkdir()
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: other-name
triggers:
  - pdf
---
# PDF Tools
"""
        )

        with pytest.raises(SkillValidationError) as exc_info:
            Skill.load(
                skill_md,
                skill_dir,
                directory_name="pdf-tools",
                validate_name=True,
            )
        assert "does not match directory" in str(exc_info.value)


class TestLoadSkillsFromDirWithSkillMd:
    """Tests for load_skills_from_dir() with SKILL.md directories."""

    def test_loads_skill_md_directories(self, tmp_path: Path) -> None:
        """Should load skills from skill-name/SKILL.md directories."""
        # Create directory structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        openhands_dir = repo_root / ".openhands"
        openhands_dir.mkdir()
        skills_dir = openhands_dir / "skills"
        skills_dir.mkdir()

        # Create AgentSkills-style skill
        pdf_skill_dir = skills_dir / "pdf-tools"
        pdf_skill_dir.mkdir()
        (pdf_skill_dir / "SKILL.md").write_text(
            """---
triggers:
  - pdf
---
# PDF Tools

Process PDF files.
"""
        )

        repo_skills, knowledge_skills = load_skills_from_dir(skills_dir)
        assert "pdf-tools" in knowledge_skills
        assert knowledge_skills["pdf-tools"].name == "pdf-tools"

    def test_loads_both_formats(self, tmp_path: Path) -> None:
        """Should load both flat .md files and SKILL.md directories."""
        # Create directory structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        openhands_dir = repo_root / ".openhands"
        openhands_dir.mkdir()
        skills_dir = openhands_dir / "skills"
        skills_dir.mkdir()

        # Create flat .md skill
        (skills_dir / "flat-skill.md").write_text(
            """---
triggers:
  - flat
---
# Flat Skill
"""
        )

        # Create AgentSkills-style skill
        dir_skill = skills_dir / "dir-skill"
        dir_skill.mkdir()
        (dir_skill / "SKILL.md").write_text(
            """---
triggers:
  - directory
---
# Directory Skill
"""
        )

        repo_skills, knowledge_skills = load_skills_from_dir(skills_dir)
        assert "flat-skill" in knowledge_skills
        assert "dir-skill" in knowledge_skills

    def test_skill_md_takes_precedence(self, tmp_path: Path) -> None:
        """Files in SKILL.md directories should not be loaded separately."""
        # Create directory structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        openhands_dir = repo_root / ".openhands"
        openhands_dir.mkdir()
        skills_dir = openhands_dir / "skills"
        skills_dir.mkdir()

        # Create AgentSkills-style skill with extra .md file
        pdf_skill_dir = skills_dir / "pdf-tools"
        pdf_skill_dir.mkdir()
        (pdf_skill_dir / "SKILL.md").write_text(
            """---
triggers:
  - pdf
---
# PDF Tools
"""
        )
        # This file should NOT be loaded as a separate skill
        (pdf_skill_dir / "extra.md").write_text(
            """---
triggers:
  - extra
---
# Extra
"""
        )

        repo_skills, knowledge_skills = load_skills_from_dir(skills_dir)
        # Should only have pdf-tools, not extra
        assert "pdf-tools" in knowledge_skills
        assert "extra" not in knowledge_skills
        assert len(knowledge_skills) == 1

    def test_validate_names_option(self, tmp_path: Path) -> None:
        """Should validate names when validate_names=True."""
        # Create directory structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        openhands_dir = repo_root / ".openhands"
        openhands_dir.mkdir()
        skills_dir = openhands_dir / "skills"
        skills_dir.mkdir()

        # Create skill with invalid name
        invalid_skill_dir = skills_dir / "Invalid_Name"
        invalid_skill_dir.mkdir()
        (invalid_skill_dir / "SKILL.md").write_text(
            """---
triggers:
  - test
---
# Invalid
"""
        )

        with pytest.raises(SkillValidationError) as exc_info:
            load_skills_from_dir(skills_dir, validate_names=True)
        assert "Invalid skill name" in str(exc_info.value)

    def test_case_insensitive_skill_md(self, tmp_path: Path) -> None:
        """Should find skill.md with different casing."""
        # Create directory structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        openhands_dir = repo_root / ".openhands"
        openhands_dir.mkdir()
        skills_dir = openhands_dir / "skills"
        skills_dir.mkdir()

        # Create skill with lowercase skill.md
        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text(
            """---
triggers:
  - test
---
# My Skill
"""
        )

        repo_skills, knowledge_skills = load_skills_from_dir(skills_dir)
        assert "my-skill" in knowledge_skills
