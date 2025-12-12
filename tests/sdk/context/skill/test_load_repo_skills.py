"""Tests for load_repo_skills functionality."""

import tempfile
from pathlib import Path

import pytest

from openhands.sdk.context.skills import (
    KeywordTrigger,
    load_repo_skills,
)


@pytest.fixture
def temp_workspace_with_skills():
    """Create a temporary workspace with .openhands/skills directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)

        # Create .openhands/skills directory
        skills_dir = workspace / ".openhands" / "skills"
        skills_dir.mkdir(parents=True)

        yield workspace, skills_dir


def test_load_repo_skills_no_directories(tmp_path):
    """Test load_repo_skills returns empty list when no .openhands directories exist."""
    skills = load_repo_skills(tmp_path)
    assert skills == []


def test_load_repo_skills_with_skills_directory(temp_workspace_with_skills):
    """Test load_repo_skills loads skills from .openhands/skills directory."""
    workspace, skills_dir = temp_workspace_with_skills

    # Create a repo skill (no triggers) and a knowledge skill (with triggers)
    (skills_dir / "repo_guidelines.md").write_text(
        "---\nname: repo_guidelines\n---\nAlways follow these guidelines."
    )
    (skills_dir / "python_help.md").write_text(
        "---\nname: python_help\ntriggers:\n  - python\n---\nPython help."
    )

    skills = load_repo_skills(workspace)
    assert len(skills) == 2

    repo_skill = next(s for s in skills if s.name == "repo_guidelines")
    knowledge_skill = next(s for s in skills if s.name == "python_help")

    assert repo_skill.trigger is None  # Repo skill
    assert isinstance(knowledge_skill.trigger, KeywordTrigger)  # Knowledge skill


def test_load_repo_skills_with_microagents_directory(tmp_path):
    """Test load_repo_skills loads from legacy .openhands/microagents directory."""
    microagents_dir = tmp_path / ".openhands" / "microagents"
    microagents_dir.mkdir(parents=True)

    (microagents_dir / "legacy_skill.md").write_text(
        "---\nname: legacy_skill\n---\nLegacy microagent skill."
    )

    skills = load_repo_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "legacy_skill"
