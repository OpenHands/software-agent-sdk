"""Tests for load_project_skills functionality."""

from openhands.sdk.context.skills import (
    KeywordTrigger,
    load_project_skills,
)


def test_load_project_skills_no_directories(tmp_path):
    """Test load_project_skills when no project skills directories exist."""
    skills = load_project_skills(tmp_path)
    assert skills == []


def test_load_project_skills_with_skills_directory(tmp_path):
    """Test load_project_skills loads from .openhands/skills directory."""
    # Create .openhands/skills directory
    skills_dir = tmp_path / ".openhands" / "skills"
    skills_dir.mkdir(parents=True)

    # Create a test skill file
    skill_file = skills_dir / "test_skill.md"
    skill_file.write_text(
        "---\nname: test_skill\ntriggers:\n  - test\n---\nThis is a test skill."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "test_skill"
    assert skills[0].content == "This is a test skill."
    assert isinstance(skills[0].trigger, KeywordTrigger)


def test_load_project_skills_with_microagents_directory(tmp_path):
    """Test load_project_skills loads from .openhands/microagents directory (legacy)."""
    # Create .openhands/microagents directory
    microagents_dir = tmp_path / ".openhands" / "microagents"
    microagents_dir.mkdir(parents=True)

    # Create a test microagent file
    microagent_file = microagents_dir / "legacy_skill.md"
    microagent_file.write_text(
        "---\n"
        "name: legacy_skill\n"
        "triggers:\n"
        "  - legacy\n"
        "---\n"
        "This is a legacy microagent skill."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "legacy_skill"
    assert skills[0].content == "This is a legacy microagent skill."


def test_load_project_skills_priority_order(tmp_path):
    """Test that skills/ directory takes precedence over microagents/."""
    # Create both directories
    skills_dir = tmp_path / ".openhands" / "skills"
    microagents_dir = tmp_path / ".openhands" / "microagents"
    skills_dir.mkdir(parents=True)
    microagents_dir.mkdir(parents=True)

    # Create duplicate skill in both directories
    (skills_dir / "duplicate.md").write_text(
        "---\nname: duplicate\n---\nFrom skills directory."
    )

    (microagents_dir / "duplicate.md").write_text(
        "---\nname: duplicate\n---\nFrom microagents directory."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "duplicate"
    # Should be from skills directory (takes precedence)
    assert skills[0].content == "From skills directory."


def test_load_project_skills_both_directories(tmp_path):
    """Test loading unique skills from both directories."""
    # Create both directories
    skills_dir = tmp_path / ".openhands" / "skills"
    microagents_dir = tmp_path / ".openhands" / "microagents"
    skills_dir.mkdir(parents=True)
    microagents_dir.mkdir(parents=True)

    # Create different skills in each directory
    (skills_dir / "skill1.md").write_text("---\nname: skill1\n---\nSkill 1 content.")
    (microagents_dir / "skill2.md").write_text(
        "---\nname: skill2\n---\nSkill 2 content."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 2
    skill_names = {s.name for s in skills}
    assert skill_names == {"skill1", "skill2"}


def test_load_project_skills_handles_errors_gracefully(tmp_path):
    """Test that errors in loading are handled gracefully."""
    # Create .openhands/skills directory
    skills_dir = tmp_path / ".openhands" / "skills"
    skills_dir.mkdir(parents=True)

    # Create an invalid skill file
    invalid_file = skills_dir / "invalid.md"
    invalid_file.write_text(
        "---\n"
        "triggers: not_a_list\n"  # Invalid: triggers must be a list
        "---\n"
        "Invalid skill."
    )

    # Should not raise exception, just return empty list
    skills = load_project_skills(tmp_path)
    assert skills == []


def test_load_project_skills_with_string_path(tmp_path):
    """Test that load_project_skills accepts string paths."""
    # Create .openhands/skills directory
    skills_dir = tmp_path / ".openhands" / "skills"
    skills_dir.mkdir(parents=True)

    # Create a test skill file
    skill_file = skills_dir / "test_skill.md"
    skill_file.write_text("---\nname: test_skill\n---\nTest skill content.")

    # Pass path as string
    skills = load_project_skills(str(tmp_path))
    assert len(skills) == 1
    assert skills[0].name == "test_skill"
