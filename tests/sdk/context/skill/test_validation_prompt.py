"""Tests for validation and prompt generation utilities (Issue #1478)."""

from pathlib import Path

from openhands.sdk.context.skills import (
    Skill,
    SkillValidationError,
    to_prompt,
    validate_skill,
)


class TestSkillValidationError:
    """Tests for enhanced SkillValidationError."""

    def test_error_without_errors_list(self) -> None:
        """Should work without errors list."""
        error = SkillValidationError("Test error")
        assert str(error) == "Test error"
        assert error.errors == []

    def test_error_with_errors_list(self) -> None:
        """Should include errors in string representation."""
        error = SkillValidationError(
            "Validation failed",
            errors=["Error 1", "Error 2"],
        )
        assert "Error 1" in str(error)
        assert "Error 2" in str(error)
        assert error.errors == ["Error 1", "Error 2"]

    def test_error_with_empty_errors_list(self) -> None:
        """Should handle empty errors list."""
        error = SkillValidationError("Test error", errors=[])
        assert str(error) == "Test error"
        assert error.errors == []


class TestValidateSkill:
    """Tests for validate_skill() function."""

    def test_validates_valid_skill(self, tmp_path: Path) -> None:
        """Should return empty list for valid skill."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
description: A test skill
---
# My Skill

This is a valid skill.
"""
        )

        errors = validate_skill(skill_dir)
        assert errors == []

    def test_error_for_nonexistent_directory(self, tmp_path: Path) -> None:
        """Should return error for nonexistent directory."""
        skill_dir = tmp_path / "nonexistent"
        errors = validate_skill(skill_dir)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_error_for_missing_skill_md(self, tmp_path: Path) -> None:
        """Should return error for missing SKILL.md."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()

        errors = validate_skill(skill_dir)
        assert len(errors) == 1
        assert "Missing SKILL.md" in errors[0]

    def test_error_for_invalid_skill_name(self, tmp_path: Path) -> None:
        """Should return error for invalid skill name."""
        skill_dir = tmp_path / "Invalid_Name"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Invalid Name Skill\n\nContent here.")

        errors = validate_skill(skill_dir)
        # Should have error about lowercase alphanumeric requirement
        assert any("lowercase" in e.lower() for e in errors)

    def test_error_for_empty_content(self, tmp_path: Path) -> None:
        """Should return error for empty SKILL.md content."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
description: A test skill
---
"""
        )

        errors = validate_skill(skill_dir)
        assert any("no content" in e.lower() for e in errors)

    def test_error_for_long_description(self, tmp_path: Path) -> None:
        """Should return error for description over 1024 chars."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        long_desc = "x" * 1025
        skill_md.write_text(
            f"""---
description: {long_desc}
---
# My Skill

Content here.
"""
        )

        errors = validate_skill(skill_dir)
        assert any("1024 characters" in e for e in errors)

    def test_error_for_invalid_mcp_tools(self, tmp_path: Path) -> None:
        """Should return error for invalid mcp_tools type."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
mcp_tools: "not a dict"
---
# My Skill

Content here.
"""
        )

        errors = validate_skill(skill_dir)
        assert any("mcp_tools must be a dictionary" in e for e in errors)

    def test_error_for_invalid_triggers(self, tmp_path: Path) -> None:
        """Should return error for invalid triggers type."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers: "not a list"
---
# My Skill

Content here.
"""
        )

        errors = validate_skill(skill_dir)
        assert any("triggers must be a list" in e for e in errors)

    def test_error_for_invalid_inputs(self, tmp_path: Path) -> None:
        """Should return error for invalid inputs type."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
inputs: "not a list"
---
# My Skill

Content here.
"""
        )

        errors = validate_skill(skill_dir)
        assert any("inputs must be a list" in e for e in errors)

    def test_validates_mcp_json(self, tmp_path: Path) -> None:
        """Should validate .mcp.json if present."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# My Skill\n\nContent here.")
        mcp_json = skill_dir / ".mcp.json"
        mcp_json.write_text("invalid json")

        errors = validate_skill(skill_dir)
        assert any(".mcp.json" in e for e in errors)

    def test_multiple_errors(self, tmp_path: Path) -> None:
        """Should return multiple errors."""
        skill_dir = tmp_path / "Invalid_Name"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers: "not a list"
---
"""
        )

        errors = validate_skill(skill_dir)
        # Should have errors for: invalid name, empty content, invalid triggers
        assert len(errors) >= 2


class TestToPrompt:
    """Tests for to_prompt() function."""

    def test_empty_skills_list(self) -> None:
        """Should return empty available_skills block."""
        result = to_prompt([])
        assert result == "<available_skills>\n</available_skills>"

    def test_single_skill_with_description(self) -> None:
        """Should generate XML for skill with description."""
        skill = Skill(
            name="pdf-tools",
            content="# PDF Tools\n\nProcess PDF files.",
            description="Extract text and tables from PDF files.",
        )
        result = to_prompt([skill])
        assert '<skill name="pdf-tools">' in result
        assert "Extract text and tables from PDF files." in result
        assert "<available_skills>" in result
        assert "</available_skills>" in result

    def test_skill_without_description_uses_content(self) -> None:
        """Should use first content line when no description."""
        skill = Skill(
            name="code-review",
            content="# Code Review\n\nReview code for bugs and style issues.",
        )
        result = to_prompt([skill])
        assert '<skill name="code-review">' in result
        assert "Review code for bugs and style issues." in result

    def test_multiple_skills(self) -> None:
        """Should generate XML for multiple skills."""
        skills = [
            Skill(
                name="pdf-tools",
                content="# PDF Tools",
                description="Process PDF files.",
            ),
            Skill(
                name="code-review",
                content="# Code Review",
                description="Review code.",
            ),
        ]
        result = to_prompt(skills)
        assert '<skill name="pdf-tools">' in result
        assert '<skill name="code-review">' in result
        assert result.count("<skill") == 2

    def test_escapes_xml_special_characters(self) -> None:
        """Should escape XML special characters."""
        skill = Skill(
            name="test-skill",
            content="# Test",
            description='Handle <tags> & "quotes" safely.',
        )
        result = to_prompt([skill])
        assert "&lt;tags&gt;" in result
        assert "&amp;" in result
        assert "&quot;quotes&quot;" in result

    def test_escapes_skill_name(self) -> None:
        """Should escape skill name."""
        skill = Skill(
            name="test&skill",
            content="# Test",
            description="Test skill.",
        )
        result = to_prompt([skill])
        assert 'name="test&amp;skill"' in result

    def test_truncates_long_content_fallback(self) -> None:
        """Should truncate long content when used as fallback."""
        long_content = "x" * 300
        skill = Skill(
            name="test-skill",
            content=f"# Test\n\n{long_content}",
        )
        result = to_prompt([skill])
        # Should be truncated to 200 chars
        assert len(result) < 400

    def test_skips_markdown_headers_in_content(self) -> None:
        """Should skip markdown headers when extracting description."""
        skill = Skill(
            name="test-skill",
            content="# Header\n## Subheader\n\nActual content here.",
        )
        result = to_prompt([skill])
        assert "Actual content here." in result
        assert "# Header" not in result

    def test_handles_empty_description_and_content(self) -> None:
        """Should handle skill with empty description and content."""
        skill = Skill(
            name="empty-skill",
            content="",
        )
        result = to_prompt([skill])
        assert '<skill name="empty-skill"></skill>' in result


class TestToPromptIntegration:
    """Integration tests for to_prompt() with loaded skills."""

    def test_with_loaded_skill(self, tmp_path: Path) -> None:
        """Should work with skills loaded from files."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
description: A test skill for processing data.
---
# My Skill

This skill processes data efficiently.
"""
        )

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        result = to_prompt([skill])

        assert '<skill name="my-skill">' in result
        assert "A test skill for processing data." in result
