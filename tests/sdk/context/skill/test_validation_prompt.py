"""Tests for prompt generation utilities (Issue #1478)."""

from openhands.sdk.context.skills import (
    Skill,
    to_prompt,
)


def test_to_prompt_generates_xml() -> None:
    """to_prompt() should generate valid XML for skills."""
    # Empty list
    assert to_prompt([]) == "<available_skills>\n</available_skills>"

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
