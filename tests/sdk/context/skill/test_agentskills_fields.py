"""Tests for AgentSkills standard fields in the Skill model."""

import pytest
from pydantic import ValidationError

from openhands.sdk.context.skills import Skill, SkillValidationError


def test_skill_with_agentskills_fields(tmp_path) -> None:
    """Skill should support AgentSkills standard fields."""
    skill_content = """---
name: pdf-processing
description: Extract text from PDF files.
license: Apache-2.0
compatibility: Requires poppler-utils
metadata:
  author: example-org
  version: "1.0"
allowed-tools: Bash(pdftotext:*) Read Write
triggers:
  - pdf
---
# PDF Processing
"""
    path = tmp_path / "pdf.md"
    path.write_text(skill_content)
    skill = Skill.load(path)

    assert skill.name == "pdf-processing"
    assert skill.description == "Extract text from PDF files."
    assert skill.license == "Apache-2.0"
    assert skill.compatibility == "Requires poppler-utils"
    assert skill.metadata == {"author": "example-org", "version": "1.0"}
    assert skill.allowed_tools == ["Bash(pdftotext:*)", "Read", "Write"]
    assert skill.match_trigger("process pdf") == "pdf"


def test_skill_allowed_tools_formats(tmp_path) -> None:
    """allowed-tools should accept string or list format."""
    # String format
    path = tmp_path / "s1.md"
    path.write_text("---\nname: s\nallowed-tools: A B\n---\n#")
    skill = Skill.load(path)
    assert skill.allowed_tools == ["A", "B"]

    # List format
    path = tmp_path / "s2.md"
    path.write_text("---\nname: s\nallowed-tools:\n  - A\n  - B\n---\n#")
    skill = Skill.load(path)
    assert skill.allowed_tools == ["A", "B"]

    # Underscore variant
    path = tmp_path / "s3.md"
    path.write_text("---\nname: s\nallowed_tools: A B\n---\n#")
    skill = Skill.load(path)
    assert skill.allowed_tools == ["A", "B"]


def test_skill_invalid_field_types(tmp_path) -> None:
    """Skill should reject invalid field types via Pydantic validation."""
    # Invalid description - Pydantic validates string type
    path = tmp_path / "invalid_desc.md"
    path.write_text("---\nname: s\ndescription:\n  - list\n---\n#")
    with pytest.raises(ValidationError, match="description"):
        Skill.load(path)

    # Invalid metadata - custom validator raises SkillValidationError
    path = tmp_path / "invalid_meta.md"
    path.write_text("---\nname: s\nmetadata: string\n---\n#")
    with pytest.raises(SkillValidationError, match="metadata must be a dictionary"):
        Skill.load(path)

    # Invalid allowed-tools - custom validator raises SkillValidationError
    path = tmp_path / "invalid_tools.md"
    path.write_text("---\nname: s\nallowed-tools: 123\n---\n#")
    with pytest.raises(SkillValidationError, match="allowed-tools must be"):
        Skill.load(path)


def test_skill_backward_compatibility(tmp_path) -> None:
    """Skills without AgentSkills fields should still work."""
    path = tmp_path / "s.md"
    path.write_text("---\nname: legacy\ntriggers:\n  - test\n---\n#")
    skill = Skill.load(path)
    assert skill.name == "legacy"
    assert skill.description is None
    assert skill.license is None
    assert skill.match_trigger("test") == "test"
