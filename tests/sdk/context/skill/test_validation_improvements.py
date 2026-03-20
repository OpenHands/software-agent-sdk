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
    # Without source, falls back to the default truncation notice
    assert DEFAULT_TRUNCATE_NOTICE in skill.description


def test_description_truncation_includes_source_path() -> None:
    """When source is set, truncation notice should reference the skill path."""
    desc = "x" * (MAX_DESCRIPTION_LENGTH + 500)
    source = "/path/to/my-skill/SKILL.md"
    skill = Skill(name="test", content="# Test", description=desc, source=source)
    assert skill.description is not None
    assert len(skill.description) == MAX_DESCRIPTION_LENGTH
    assert source in skill.description
