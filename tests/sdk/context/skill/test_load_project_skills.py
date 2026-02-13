"""Tests for load_project_skills functionality."""

from openhands.sdk.context.skills import (
    KeywordTrigger,
    load_project_skills,
)


def test_load_project_skills_no_directories(tmp_path):
    """Test load_project_skills when no project skills directories exist."""
    skills = load_project_skills(tmp_path)
    assert skills == []


def test_load_project_skills_agents_md_without_skills_directory(tmp_path):
    """Test that AGENTS.md is loaded even when .openhands/skills doesn't exist.

    This is a regression test for the bug where third-party skill files like
    AGENTS.md were not loaded when the .openhands/skills directory didn't exist.
    """
    # Create AGENTS.md in the work directory (no .openhands/skills)
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Project Guidelines\n\nThis is the AGENTS.md content.")

    skills = load_project_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "agents"
    assert "Project Guidelines" in skills[0].content
    assert skills[0].trigger is None  # Third-party skills are always active


def test_load_project_skills_agents_md_case_insensitive(tmp_path):
    """Test that AGENTS.md is loaded with case-insensitive matching."""
    # Create agents.md (lowercase) in the work directory
    agents_md = tmp_path / "agents.md"
    agents_md.write_text("# Lowercase agents.md content")

    skills = load_project_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "agents"


def test_load_project_skills_multiple_third_party_files(tmp_path):
    """Test loading multiple third-party skill files."""
    # Create AGENTS.md
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md content")

    # Create .cursorrules
    (tmp_path / ".cursorrules").write_text("# Cursor rules content")

    skills = load_project_skills(tmp_path)
    assert len(skills) == 2
    skill_names = {s.name for s in skills}
    assert "agents" in skill_names
    assert "cursorrules" in skill_names


def test_load_project_skills_third_party_with_skills_directory(tmp_path):
    """Test third-party files are loaded alongside skills from .openhands/skills."""
    # Create AGENTS.md in work directory
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md content")

    # Create .openhands/skills directory with a skill
    skills_dir = tmp_path / ".openhands" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "test_skill.md").write_text(
        "---\nname: test_skill\ntriggers:\n  - test\n---\nTest skill content."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 2
    skill_names = {s.name for s in skills}
    assert "agents" in skill_names
    assert "test_skill" in skill_names


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


def test_load_project_skills_with_agents_directory(tmp_path):
    """Test load_project_skills loads from .agents/skills directory."""
    # Create .agents/skills directory
    skills_dir = tmp_path / ".agents" / "skills"
    skills_dir.mkdir(parents=True)

    # Create a test skill file
    skill_file = skills_dir / "agent_skill.md"
    skill_file.write_text(
        "---\nname: agent_skill\ntriggers:\n  - agent\n---\nAgent skill content."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "agent_skill"
    assert skills[0].content == "Agent skill content."
    assert isinstance(skills[0].trigger, KeywordTrigger)


def test_load_project_skills_agents_directory_precedence(tmp_path):
    """Test .agents/skills takes precedence over other directories."""
    agents_dir = tmp_path / ".agents" / "skills"
    skills_dir = tmp_path / ".openhands" / "skills"
    microagents_dir = tmp_path / ".openhands" / "microagents"
    agents_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    microagents_dir.mkdir(parents=True)

    (agents_dir / "duplicate.md").write_text(
        "---\nname: duplicate\n---\nFrom .agents/skills."
    )
    (skills_dir / "duplicate.md").write_text(
        "---\nname: duplicate\n---\nFrom .openhands/skills."
    )
    (microagents_dir / "duplicate.md").write_text(
        "---\nname: duplicate\n---\nFrom .openhands/microagents."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "duplicate"
    assert skills[0].content == "From .agents/skills."


def test_load_project_skills_merges_agents_and_openhands(tmp_path):
    """Test loading unique skills from .agents/skills and .openhands/skills."""
    agents_dir = tmp_path / ".agents" / "skills"
    openhands_dir = tmp_path / ".openhands" / "skills"
    agents_dir.mkdir(parents=True)
    openhands_dir.mkdir(parents=True)

    (agents_dir / "agent_skill.md").write_text(
        "---\nname: agent_skill\n---\nAgent skill content."
    )
    (openhands_dir / "legacy_skill.md").write_text(
        "---\nname: legacy_skill\n---\nLegacy skill content."
    )

    skills = load_project_skills(tmp_path)
    assert len(skills) == 2
    skill_names = {skill.name for skill in skills}
    assert skill_names == {"agent_skill", "legacy_skill"}


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
