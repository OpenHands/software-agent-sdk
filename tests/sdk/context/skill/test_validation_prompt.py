"""Tests for prompt generation utilities (Issue #1478)."""

from openhands.sdk.context.skills import (
    Skill,
    to_prompt,
)


def test_to_prompt_generates_xml() -> None:
    """to_prompt() should generate valid XML for skills."""
    # Empty list shows "no available skills"
    assert (
        to_prompt([])
        == "<available_skills>\n  no available skills\n</available_skills>"
    )

    # Single skill with description
    skill = Skill(name="pdf-tools", content="# PDF", description="Process PDFs.")
    result = to_prompt([skill])
    assert '<skill name="pdf-tools">' in result
    assert "Process PDFs." in result
    assert "<available_skills>" in result

    # Multiple skills
    skills = [
        Skill(name="pdf-tools", content="# PDF", description="Process PDFs."),
        Skill(name="code-review", content="# Code", description="Review code."),
    ]
    result = to_prompt(skills)
    assert result.count("<skill") == 2


def test_to_prompt_escapes_xml() -> None:
    """to_prompt() should escape XML special characters."""
    skill = Skill(
        name="test", content="# Test", description='Handle <tags> & "quotes".'
    )
    result = to_prompt([skill])
    assert "&lt;tags&gt;" in result
    assert "&amp;" in result
    assert "&quot;quotes&quot;" in result


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
    # Should indicate truncation for remaining content
    assert "characters truncated" in result
    assert "View /skills/test.md for complete information" in result


def test_to_prompt_truncates_long_descriptions() -> None:
    """to_prompt() should truncate long descriptions with indicator."""
    long_desc = "A" * 250  # 250 characters
    skill = Skill(name="test", content="# Test", description=long_desc)
    result = to_prompt([skill])

    # Should contain truncation indicator
    assert "... [50 characters truncated]" in result
    # Should contain first 200 chars
    assert "A" * 200 in result


def test_to_prompt_truncation_includes_source() -> None:
    """to_prompt() should include source path in truncation message."""
    long_desc = "B" * 250
    skill = Skill(
        name="test",
        content="# Test",
        description=long_desc,
        source="/path/to/skill.md",
    )
    result = to_prompt([skill])

    assert "... [50 characters truncated" in result
    assert "View /path/to/skill.md for complete information]" in result
