"""Tests for skill validation improvements."""

import pytest

from openhands.sdk.context.skills import (
    Skill,
    SkillValidationError,
    validate_skill_name,
)


class TestSkillValidationError:
    """Tests for enhanced SkillValidationError."""

    def test_with_errors_list(self) -> None:
        """SkillValidationError should accept and format errors list."""
        errors = ["Missing SKILL.md", "Invalid name format"]
        exc = SkillValidationError("Validation failed", errors=errors)
        assert exc.errors == errors
        assert "Missing SKILL.md" in str(exc)
        assert "Invalid name format" in str(exc)

    def test_without_errors_list(self) -> None:
        """SkillValidationError should work without errors list."""
        exc = SkillValidationError("Simple error")
        assert exc.errors == []
        assert str(exc) == "Simple error"

    def test_default_message(self) -> None:
        """SkillValidationError should have default message."""
        exc = SkillValidationError()
        assert str(exc) == "Skill validation failed"


class TestValidateSkillName:
    """Tests for validate_skill_name export."""

    def test_valid_names(self) -> None:
        """validate_skill_name should return empty list for valid names."""
        assert validate_skill_name("pdf-tools") == []
        assert validate_skill_name("code-review") == []
        assert validate_skill_name("my-skill-123") == []
        assert validate_skill_name("a") == []

    def test_invalid_names(self) -> None:
        """validate_skill_name should return errors for invalid names."""
        assert len(validate_skill_name("PDF-Tools")) > 0  # uppercase
        assert len(validate_skill_name("my_skill")) > 0  # underscore
        assert len(validate_skill_name("my skill")) > 0  # space
        assert len(validate_skill_name("")) > 0  # empty


class TestDescriptionValidation:
    """Tests for description length validation."""

    def test_valid_description(self) -> None:
        """Skill should accept valid description."""
        skill = Skill(name="test", content="# Test", description="A short description.")
        assert skill.description == "A short description."

    def test_description_at_limit(self) -> None:
        """Skill should accept description at 1024 chars."""
        desc = "x" * 1024
        skill = Skill(name="test", content="# Test", description=desc)
        assert skill.description is not None
        assert len(skill.description) == 1024

    def test_description_exceeds_limit(self) -> None:
        """Skill should reject description over 1024 chars."""
        desc = "x" * 1025
        with pytest.raises(SkillValidationError) as exc_info:
            Skill(name="test", content="# Test", description=desc)
        assert "1024 characters" in str(exc_info.value)

    def test_none_description(self) -> None:
        """Skill should accept None description."""
        skill = Skill(name="test", content="# Test", description=None)
        assert skill.description is None
