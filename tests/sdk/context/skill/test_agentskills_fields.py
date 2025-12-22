"""Tests for AgentSkills standard fields in the Skill model."""

from pathlib import Path

import pytest

from openhands.sdk.context.skills import Skill, SkillValidationError


def test_skill_with_all_agentskills_fields():
    """Test loading a skill with all AgentSkills standard fields."""
    skill_content = """---
name: pdf-processing
description: Extract text and tables from PDF files, fill forms, merge documents.
license: Apache-2.0
compatibility: Requires poppler-utils and ghostscript
metadata:
  author: example-org
  version: "1.0"
allowed-tools: Bash(pdftotext:*) Read Write
---

# PDF Processing Skill

Instructions for processing PDF files.
"""
    skill = Skill.load(Path("pdf-processing.md"), file_content=skill_content)

    assert skill.name == "pdf-processing"
    assert skill.description == (
        "Extract text and tables from PDF files, fill forms, merge documents."
    )
    assert skill.license == "Apache-2.0"
    assert skill.compatibility == "Requires poppler-utils and ghostscript"
    assert skill.metadata == {"author": "example-org", "version": "1.0"}
    assert skill.allowed_tools == ["Bash(pdftotext:*)", "Read", "Write"]
    assert skill.trigger is None  # No triggers = always active


def test_skill_with_description_only():
    """Test loading a skill with only description field."""
    skill_content = """---
name: simple-skill
description: A simple skill for testing.
---

# Simple Skill

Content here.
"""
    skill = Skill.load(Path("simple.md"), file_content=skill_content)

    assert skill.name == "simple-skill"
    assert skill.description == "A simple skill for testing."
    assert skill.license is None
    assert skill.compatibility is None
    assert skill.metadata is None
    assert skill.allowed_tools is None


def test_skill_with_allowed_tools_as_list():
    """Test loading a skill with allowed-tools as a YAML list."""
    skill_content = """---
name: list-tools-skill
description: Skill with tools as list.
allowed-tools:
  - Bash(git:*)
  - Read
  - Write
---

# List Tools Skill

Content here.
"""
    skill = Skill.load(Path("list-tools.md"), file_content=skill_content)

    assert skill.allowed_tools == ["Bash(git:*)", "Read", "Write"]


def test_skill_with_allowed_tools_underscore():
    """Test loading a skill with allowed_tools (underscore variant)."""
    skill_content = """---
name: underscore-tools-skill
description: Skill with underscore variant.
allowed_tools: Bash Read Write
---

# Underscore Tools Skill

Content here.
"""
    skill = Skill.load(Path("underscore-tools.md"), file_content=skill_content)

    assert skill.allowed_tools == ["Bash", "Read", "Write"]


def test_skill_with_metadata_numeric_values():
    """Test that metadata values are converted to strings."""
    skill_content = """---
name: numeric-metadata-skill
description: Skill with numeric metadata values.
metadata:
  version: 2
  priority: 1.5
  enabled: true
---

# Numeric Metadata Skill

Content here.
"""
    skill = Skill.load(Path("numeric-metadata.md"), file_content=skill_content)

    # All values should be converted to strings
    assert skill.metadata == {
        "version": "2",
        "priority": "1.5",
        "enabled": "True",
    }


def test_skill_agentskills_fields_with_triggers():
    """Test AgentSkills fields work with keyword triggers."""
    skill_content = """---
name: triggered-skill
description: A skill that activates on keywords.
license: MIT
triggers:
  - pdf
  - document
---

# Triggered Skill

Content here.
"""
    skill = Skill.load(Path("triggered.md"), file_content=skill_content)

    assert skill.name == "triggered-skill"
    assert skill.description == "A skill that activates on keywords."
    assert skill.license == "MIT"
    assert skill.trigger is not None
    assert skill.match_trigger("process this pdf") == "pdf"


def test_skill_agentskills_fields_with_task_trigger():
    """Test AgentSkills fields work with task triggers."""
    skill_content = """---
name: task-skill
description: A task skill with inputs.
compatibility: Requires Python 3.12+
inputs:
  - name: filename
    description: The file to process
---

# Task Skill

Process ${filename} here.
"""
    skill = Skill.load(Path("task.md"), file_content=skill_content)

    assert skill.name == "task-skill"
    assert skill.description == "A task skill with inputs."
    assert skill.compatibility == "Requires Python 3.12+"
    assert len(skill.inputs) == 1
    assert skill.inputs[0].name == "filename"


def test_skill_invalid_description_type():
    """Test that non-string description raises error."""
    skill_content = """---
name: invalid-desc
description:
  - not
  - a
  - string
---

Content.
"""
    with pytest.raises(SkillValidationError) as excinfo:
        Skill.load(Path("invalid.md"), file_content=skill_content)

    assert "description must be a string" in str(excinfo.value)


def test_skill_invalid_license_type():
    """Test that non-string license raises error."""
    skill_content = """---
name: invalid-license
license: 123
---

Content.
"""
    with pytest.raises(SkillValidationError) as excinfo:
        Skill.load(Path("invalid.md"), file_content=skill_content)

    assert "license must be a string" in str(excinfo.value)


def test_skill_invalid_compatibility_type():
    """Test that non-string compatibility raises error."""
    skill_content = """---
name: invalid-compat
compatibility:
  requires: git
---

Content.
"""
    with pytest.raises(SkillValidationError) as excinfo:
        Skill.load(Path("invalid.md"), file_content=skill_content)

    assert "compatibility must be a string" in str(excinfo.value)


def test_skill_invalid_metadata_type():
    """Test that non-dict metadata raises error."""
    skill_content = """---
name: invalid-meta
metadata: not-a-dict
---

Content.
"""
    with pytest.raises(SkillValidationError) as excinfo:
        Skill.load(Path("invalid.md"), file_content=skill_content)

    assert "metadata must be a dictionary" in str(excinfo.value)


def test_skill_invalid_allowed_tools_type():
    """Test that invalid allowed-tools type raises error."""
    skill_content = """---
name: invalid-tools
allowed-tools: 123
---

Content.
"""
    with pytest.raises(SkillValidationError) as excinfo:
        Skill.load(Path("invalid.md"), file_content=skill_content)

    assert "allowed-tools must be a string or list" in str(excinfo.value)


def test_skill_serialization_with_agentskills_fields():
    """Test that AgentSkills fields serialize and deserialize correctly."""
    skill = Skill(
        name="test-skill",
        content="Test content",
        description="Test description",
        license="MIT",
        compatibility="Python 3.12+",
        metadata={"author": "test", "version": "1.0"},
        allowed_tools=["Read", "Write"],
    )

    # Serialize
    serialized = skill.model_dump()
    assert serialized["description"] == "Test description"
    assert serialized["license"] == "MIT"
    assert serialized["compatibility"] == "Python 3.12+"
    assert serialized["metadata"] == {"author": "test", "version": "1.0"}
    assert serialized["allowed_tools"] == ["Read", "Write"]

    # Deserialize
    deserialized = Skill.model_validate(serialized)
    assert deserialized.description == "Test description"
    assert deserialized.license == "MIT"
    assert deserialized.compatibility == "Python 3.12+"
    assert deserialized.metadata == {"author": "test", "version": "1.0"}
    assert deserialized.allowed_tools == ["Read", "Write"]


def test_skill_backward_compatibility():
    """Test that existing skills without AgentSkills fields still work."""
    skill_content = """---
name: legacy-skill
triggers:
  - legacy
---

# Legacy Skill

This skill has no AgentSkills fields.
"""
    skill = Skill.load(Path("legacy.md"), file_content=skill_content)

    assert skill.name == "legacy-skill"
    assert skill.description is None
    assert skill.license is None
    assert skill.compatibility is None
    assert skill.metadata is None
    assert skill.allowed_tools is None
    assert skill.match_trigger("use legacy feature") == "legacy"
