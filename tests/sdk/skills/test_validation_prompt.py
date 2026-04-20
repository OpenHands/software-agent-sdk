"""Tests for prompt generation utilities (Issue #1478)."""

from openhands.sdk.skills import (
    Skill,
    to_prompt,
)


def test_to_prompt_generates_xml() -> None:
    """to_prompt() should generate valid XML for skills in AgentSkills format."""
    # Empty list shows "no available skills"
    assert (
        to_prompt([])
        == "<available_skills>\n  no available skills\n</available_skills>"
    )

    # Single skill with description
    skill = Skill(name="pdf-tools", content="# PDF", description="Process PDFs.")
    result = to_prompt([skill])
    assert "<skill>" in result
    assert "<name>pdf-tools</name>" in result
    assert "<description>Process PDFs.</description>" in result
    assert "<available_skills>" in result

    # Multiple skills
    skills = [
        Skill(name="pdf-tools", content="# PDF", description="Process PDFs."),
        Skill(name="code-review", content="# Code", description="Review code."),
    ]
    result = to_prompt(skills)
    assert result.count("<skill>") == 2


def test_to_prompt_never_emits_location() -> None:
    """to_prompt() must not emit <location>: invoke_skill is the only entry
    point and the agent must not be given the file path."""
    skill = Skill(
        name="pdf-tools",
        content="# PDF",
        description="Process PDFs.",
        source="/path/to/skill.md",
    )
    result = to_prompt([skill])
    assert "<location>" not in result
    assert "/path/to/skill.md" not in result


def test_to_prompt_escapes_xml() -> None:
    """to_prompt() should escape XML special characters."""
    skill = Skill(
        name="test", content="# Test", description='Handle <tags> & "quotes".'
    )
    result = to_prompt([skill])
    assert "&lt;tags&gt;" in result
    assert "&amp;" in result
    # Quotes don't need escaping in XML element content (only in attributes)
    assert '"quotes"' in result


def test_to_prompt_uses_content_fallback() -> None:
    """to_prompt() should use content when no description."""
    skill = Skill(name="test", content="# Header\n\nActual content here.")
    result = to_prompt([skill])
    assert "Actual content here." in result
    assert "# Header" not in result


def test_to_prompt_content_fallback_counts_remaining_as_truncated() -> None:
    """to_prompt() should count content after first line as truncated."""
    # Content with header, description line, and additional content
    content = "# Header\n\nFirst line used as description.\n\nMore content here."
    skill = Skill(name="test", content=content, source="/skills/test.md")
    result = to_prompt([skill])

    # Should use first non-header line as description
    assert "First line used as description." in result
    # Should indicate truncation for remaining content and point the agent at
    # invoke_skill (not the file path) as the way to load the full content.
    assert "characters truncated" in result
    assert 'invoke_skill(name="test")' in result
    assert "/skills/test.md" not in result


def test_to_prompt_truncates_long_descriptions() -> None:
    """to_prompt() should truncate long descriptions with indicator."""
    skill = Skill(name="test", content="# Test", description="short")
    skill.description = "A" * 1034
    result = to_prompt([skill])

    # Should contain truncation indicator pointing at invoke_skill
    assert "... [10 characters truncated" in result
    assert 'invoke_skill(name="test")' in result
    # Should contain first 1024 chars
    assert "A" * 1024 in result


def test_to_prompt_truncation_points_at_invoke_skill_not_source() -> None:
    """Truncation message must direct the agent to invoke_skill, not the
    skill's source path."""
    skill = Skill(
        name="test",
        content="# Test",
        description="short",
        source="/path/to/skill.md",
    )
    skill.description = "B" * 1034
    result = to_prompt([skill])

    assert "... [10 characters truncated" in result
    assert 'invoke_skill(name="test")' in result
    assert "/path/to/skill.md" not in result
