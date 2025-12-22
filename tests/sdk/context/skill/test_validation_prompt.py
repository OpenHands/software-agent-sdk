"""Tests for validation and prompt generation utilities (Issue #1478)."""

from pathlib import Path

from openhands.sdk.context.skills import (
    Skill,
    SkillValidationError,
    to_prompt,
    validate_skill,
)


def test_skill_validation_error_with_errors_list() -> None:
    """SkillValidationError should include errors in string representation."""
    # Without errors
    error = SkillValidationError("Test error")
    assert str(error) == "Test error"
    assert error.errors == []

    # With errors
    error = SkillValidationError("Validation failed", errors=["Error 1", "Error 2"])
    assert "Error 1" in str(error)
    assert "Error 2" in str(error)
    assert error.errors == ["Error 1", "Error 2"]


def test_validate_skill_valid_directory(tmp_path: Path) -> None:
    """validate_skill() should return empty list for valid skill."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Test\n---\n# My Skill\n\nContent."
    )

    errors = validate_skill(skill_dir)
    assert errors == []


def test_validate_skill_errors(tmp_path: Path) -> None:
    """validate_skill() should return errors for invalid skills."""
    # Nonexistent directory
    errors = validate_skill(tmp_path / "nonexistent")
    assert any("does not exist" in e for e in errors)

    # Missing SKILL.md
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    errors = validate_skill(empty_dir)
    assert any("Missing SKILL.md" in e for e in errors)

    # Invalid skill name
    bad_name = tmp_path / "Invalid_Name"
    bad_name.mkdir()
    (bad_name / "SKILL.md").write_text("# Test\n\nContent.")
    errors = validate_skill(bad_name)
    assert any("lowercase" in e.lower() for e in errors)

    # Empty content
    empty_content = tmp_path / "empty-content"
    empty_content.mkdir()
    (empty_content / "SKILL.md").write_text("---\ndescription: Test\n---\n")
    errors = validate_skill(empty_content)
    assert any("no content" in e.lower() for e in errors)


def test_to_prompt_generates_xml() -> None:
    """to_prompt() should generate valid XML for skills with nested elements."""
    # Empty list
    assert to_prompt([]) == "<available_skills>\n</available_skills>"

    # Single skill with description and source
    skill = Skill(
        name="pdf-tools",
        content="# PDF",
        description="Process PDFs.",
        source="/path/to/pdf-tools/SKILL.md",
    )
    result = to_prompt([skill])
    assert "<skill>" in result
    assert "<name>pdf-tools</name>" in result
    assert "<description>Process PDFs.</description>" in result
    assert "<location>/path/to/pdf-tools/SKILL.md</location>" in result
    assert "<available_skills>" in result

    # Multiple skills
    skills = [
        Skill(name="pdf-tools", content="# PDF", description="Process PDFs."),
        Skill(name="code-review", content="# Code", description="Review code."),
    ]
    result = to_prompt(skills)
    assert result.count("<skill>") == 2
    assert result.count("</skill>") == 2


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


def test_to_prompt_includes_resources() -> None:
    """to_prompt() should include resource directories when available."""
    from openhands.sdk.context.skills import SkillResources

    resources = SkillResources(
        skill_root="/path/to/skill",
        scripts=["run.sh"],
        references=["guide.md"],
        assets=[],
    )
    skill = Skill(
        name="test-skill",
        content="# Test",
        description="A test skill",
        source="/path/to/skill/SKILL.md",
        resources=resources,
    )
    result = to_prompt([skill])
    assert "<resources>" in result
    assert "<scripts_dir>/path/to/skill/scripts</scripts_dir>" in result
    assert "<references_dir>/path/to/skill/references</references_dir>" in result
    # assets_dir should not be included since assets list is empty
    assert "<assets_dir>" not in result


def test_to_prompt_without_location() -> None:
    """to_prompt() should omit location when include_location=False."""
    skill = Skill(
        name="test",
        content="# Test",
        description="Test skill",
        source="/path/to/SKILL.md",
    )
    result = to_prompt([skill], include_location=False)
    assert "<name>test</name>" in result
    assert "<description>Test skill</description>" in result
    assert "<location>" not in result
