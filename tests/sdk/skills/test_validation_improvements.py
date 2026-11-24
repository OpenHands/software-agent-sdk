"""Tests for skill validation improvements."""

from openhands.sdk.skills import Skill


MAX_DESCRIPTION_LENGTH = 1024


def test_description_at_limit() -> None:
    """Skill should accept description at 1024 chars."""
    desc = "x" * MAX_DESCRIPTION_LENGTH
    skill = Skill(name="test", content="# Test", description=desc)
    assert skill.description is not None
    assert len(skill.description) == MAX_DESCRIPTION_LENGTH


def test_description_exceeds_limit_is_truncated() -> None:
    """Skill should truncate description over 1024 chars instead of erroring."""
    desc = "x" * (MAX_DESCRIPTION_LENGTH + 100)
    skill = Skill(name="test", content="# Test", description=desc)
    assert skill.description is not None
    assert len(skill.description) == MAX_DESCRIPTION_LENGTH
    assert 'Call invoke_skill(name="test") to load the full skill.' in (
        skill.description
    )


def test_description_truncation_points_at_invoke_skill_not_source() -> None:
    """The truncation notice names the skill for invoke_skill and never the
    file: to_prompt() leaves the location out on purpose (#6644)."""
    desc = "x" * (MAX_DESCRIPTION_LENGTH + 500)
    source = "/path/to/my-skill/SKILL.md"
    skill = Skill(name="test", content="# Test", description=desc, source=source)
    assert skill.description is not None
    assert len(skill.description) == MAX_DESCRIPTION_LENGTH
    assert source not in skill.description
    assert 'invoke_skill(name="test")' in skill.description
