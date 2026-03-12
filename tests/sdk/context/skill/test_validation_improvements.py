"""Tests for skill validation improvements."""

from openhands.sdk.context.skills import Skill
from openhands.sdk.utils import DEFAULT_TRUNCATE_NOTICE


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
    # maybe_truncate inserts a notice in the middle so the agent knows
    # the content was clipped and where to find the full version
    assert DEFAULT_TRUNCATE_NOTICE in skill.description
