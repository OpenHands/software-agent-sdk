"""Tests for skill validation improvements."""

import pytest

from openhands.sdk.context.skills import Skill, SkillValidationError


def test_description_at_limit() -> None:
    """Skill should accept description at 1024 chars."""
    desc = "x" * 1024
    skill = Skill(name="test", content="# Test", description=desc)
    assert skill.description is not None
    assert len(skill.description) == 1024


def test_description_exceeds_limit() -> None:
    """Skill should reject description over 1024 chars."""
    desc = "x" * 1025
    with pytest.raises(SkillValidationError) as exc_info:
        Skill(name="test", content="# Test", description=desc)
    assert "1024 characters" in str(exc_info.value)
