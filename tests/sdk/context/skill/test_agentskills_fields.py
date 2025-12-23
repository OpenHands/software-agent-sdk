"""Tests for AgentSkills standard fields in the Skill model."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk.context.skills import Skill, SkillValidationError


def test_skill_with_agentskills_fields() -> None:
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
    skill = Skill.load(Path("pdf.md"), file_content=skill_content)

    assert skill.name == "pdf-processing"
    assert skill.description == "Extract text from PDF files."
    assert skill.license == "Apache-2.0"
    assert skill.compatibility == "Requires poppler-utils"
    assert skill.metadata == {"author": "example-org", "version": "1.0"}
    assert skill.allowed_tools == ["Bash(pdftotext:*)", "Read", "Write"]
    assert skill.match_trigger("process pdf") == "pdf"


def test_skill_allowed_tools_formats() -> None:
    """allowed-tools should accept string or list format."""
    # String format
    skill = Skill.load(
        Path("s.md"), file_content="---\nname: s\nallowed-tools: A B\n---\n#"
    )
    assert skill.allowed_tools == ["A", "B"]

    # List format
    skill = Skill.load(
        Path("s.md"), file_content="---\nname: s\nallowed-tools:\n  - A\n  - B\n---\n#"
    )
    assert skill.allowed_tools == ["A", "B"]

    # Underscore variant
    skill = Skill.load(
        Path("s.md"), file_content="---\nname: s\nallowed_tools: A B\n---\n#"
    )
    assert skill.allowed_tools == ["A", "B"]


def test_skill_invalid_field_types() -> None:
    """Skill should reject invalid field types via Pydantic validation."""
    # Invalid description - Pydantic validates string type
    with pytest.raises(ValidationError, match="description"):
        Skill.load(
            Path("s.md"), file_content="---\nname: s\ndescription:\n  - list\n---\n#"
        )

    # Invalid metadata - custom validator raises SkillValidationError
    with pytest.raises(SkillValidationError, match="metadata must be a dictionary"):
        Skill.load(Path("s.md"), file_content="---\nname: s\nmetadata: string\n---\n#")

    # Invalid allowed-tools - custom validator raises SkillValidationError
    with pytest.raises(SkillValidationError, match="allowed-tools must be"):
        Skill.load(
            Path("s.md"), file_content="---\nname: s\nallowed-tools: 123\n---\n#"
        )


def test_skill_backward_compatibility() -> None:
    """Skills without AgentSkills fields should still work."""
    skill = Skill.load(
        Path("s.md"), file_content="---\nname: legacy\ntriggers:\n  - test\n---\n#"
    )
    assert skill.name == "legacy"
    assert skill.description is None
    assert skill.license is None
    assert skill.match_trigger("test") == "test"
