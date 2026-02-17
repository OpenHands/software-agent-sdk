"""Tests for load_user_skills functionality."""

import tempfile
from pathlib import Path

import pytest

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import (
    KeywordTrigger,
    Skill,
    load_user_skills,
)


@pytest.fixture
def temp_user_skills_dir():
    """Create a temporary user skills directory structure."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        # Create .agents/skills directory
        agents_dir = root / ".agents" / "skills"
        agents_dir.mkdir(parents=True)

        # Create .openhands/skills directory
        skills_dir = root / ".openhands" / "skills"
        skills_dir.mkdir(parents=True)

        yield root, agents_dir, skills_dir


@pytest.fixture
def temp_microagents_dir():
    """Create a temporary microagents directory structure."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        # Create .openhands/microagents directory
        microagents_dir = root / ".openhands" / "microagents"
        microagents_dir.mkdir(parents=True)

        yield root, microagents_dir


def test_load_user_skills_no_directories(tmp_path):
    """Test load_user_skills when no user skills directories exist."""
    # Point USER_SKILLS_DIRS to non-existent directories
    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [
            tmp_path / "nonexistent1",
            tmp_path / "nonexistent2",
        ]
        skills = load_user_skills()
        assert skills == []
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_load_user_skills_with_agents_directory(temp_user_skills_dir):
    """Test load_user_skills loads from .agents/skills directory."""
    root, agents_dir, _ = temp_user_skills_dir

    # Create a test skill file
    skill_file = agents_dir / "agent_skill.md"
    skill_file.write_text(
        "---\nname: agent_skill\ntriggers:\n  - agent\n---\nAgent skill content."
    )

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [agents_dir]
        skills = load_user_skills()
        assert len(skills) == 1
        assert skills[0].name == "agent_skill"
        assert skills[0].content == "Agent skill content."
        assert isinstance(skills[0].trigger, KeywordTrigger)
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_load_user_skills_with_skills_directory(temp_user_skills_dir):
    """Test load_user_skills loads from .openhands/skills directory."""
    root, _, skills_dir = temp_user_skills_dir

    # Create a test skill file
    skill_file = skills_dir / "test_skill.md"
    skill_file.write_text(
        "---\nname: test_skill\ntriggers:\n  - test\n---\nThis is a test skill."
    )

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [skills_dir]
        skills = load_user_skills()
        assert len(skills) == 1
        assert skills[0].name == "test_skill"
        assert skills[0].content == "This is a test skill."
        assert isinstance(skills[0].trigger, KeywordTrigger)
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_load_user_skills_with_microagents_directory(temp_microagents_dir):
    """Test load_user_skills loads from microagents directory (legacy)."""
    root, microagents_dir = temp_microagents_dir

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

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [microagents_dir]
        skills = load_user_skills()
        assert len(skills) == 1
        assert skills[0].name == "legacy_skill"
        assert skills[0].content == "This is a legacy microagent skill."
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_load_user_skills_priority_order(tmp_path):
    """Test precedence .agents/skills > .openhands/skills > microagents."""
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

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [agents_dir, skills_dir, microagents_dir]
        skills = load_user_skills()
        assert len(skills) == 1
        assert skills[0].name == "duplicate"
        assert skills[0].content == "From .agents/skills."
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_load_user_skills_merges_all_directories(tmp_path):
    """Test loading unique skills from .agents/skills, .openhands/skills,
    microagents.
    """
    agents_dir = tmp_path / ".agents" / "skills"
    skills_dir = tmp_path / ".openhands" / "skills"
    microagents_dir = tmp_path / ".openhands" / "microagents"
    agents_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    microagents_dir.mkdir(parents=True)

    (agents_dir / "agent_skill.md").write_text(
        "---\nname: agent_skill\n---\nAgent skill content."
    )
    (skills_dir / "skill1.md").write_text("---\nname: skill1\n---\nSkill 1 content.")
    (microagents_dir / "skill2.md").write_text(
        "---\nname: skill2\n---\nSkill 2 content."
    )

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [agents_dir, skills_dir, microagents_dir]
        skills = load_user_skills()
        assert len(skills) == 3
        skill_names = {s.name for s in skills}
        assert skill_names == {"agent_skill", "skill1", "skill2"}
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_load_user_skills_handles_errors_gracefully(temp_user_skills_dir):
    """Test that errors in loading are handled gracefully."""
    root, _, skills_dir = temp_user_skills_dir

    # Create an invalid skill file
    invalid_file = skills_dir / "invalid.md"
    invalid_file.write_text(
        "---\n"
        "triggers: not_a_list\n"  # Invalid: triggers must be a list
        "---\n"
        "Invalid skill."
    )

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [skills_dir]
        # Should not raise exception, just return empty list
        skills = load_user_skills()
        assert skills == []
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_agent_context_loads_user_skills_by_default(temp_user_skills_dir):
    """Test that AgentContext loads user skills when enabled."""
    root, _, skills_dir = temp_user_skills_dir

    # Create a test skill
    skill_file = skills_dir / "auto_skill.md"
    skill_file.write_text("---\nname: auto_skill\n---\nAutomatically loaded skill.")

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [skills_dir]
        context = AgentContext(load_user_skills=True)
        skill_names = [s.name for s in context.skills]
        assert "auto_skill" in skill_names
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_agent_context_can_disable_user_skills_loading():
    """Test that user skills loading can be disabled."""
    context = AgentContext(load_user_skills=False)
    assert context.skills == []


def test_agent_context_merges_explicit_and_user_skills(temp_user_skills_dir):
    """Test that explicit skills and user skills are merged correctly."""
    root, _, skills_dir = temp_user_skills_dir

    # Create user skill
    user_skill_file = skills_dir / "user_skill.md"
    user_skill_file.write_text("---\nname: user_skill\n---\nUser skill content.")

    # Create explicit skill
    explicit_skill = Skill(
        name="explicit_skill",
        content="Explicit skill content.",
        trigger=None,
    )

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [skills_dir]
        context = AgentContext(skills=[explicit_skill], load_user_skills=True)
        skill_names = [s.name for s in context.skills]
        assert "explicit_skill" in skill_names
        assert "user_skill" in skill_names
        assert len(context.skills) == 2
    finally:
        skill.USER_SKILLS_DIRS = original_dirs


def test_agent_context_explicit_skill_takes_precedence(temp_user_skills_dir):
    """Test that explicitly provided skills take precedence over user skills."""
    root, _, skills_dir = temp_user_skills_dir

    # Create user skill with same name
    user_skill_file = skills_dir / "duplicate.md"
    user_skill_file.write_text("---\nname: duplicate\n---\nUser skill content.")

    # Create explicit skill with same name
    explicit_skill = Skill(
        name="duplicate",
        content="Explicit skill content.",
        trigger=None,
    )

    from openhands.sdk.context.skills import skill

    original_dirs = skill.USER_SKILLS_DIRS
    try:
        skill.USER_SKILLS_DIRS = [skills_dir]
        context = AgentContext(skills=[explicit_skill], load_user_skills=True)
        assert len(context.skills) == 1
        # Explicit skill should be used, not the user skill
        assert context.skills[0].content == "Explicit skill content."
    finally:
        skill.USER_SKILLS_DIRS = original_dirs
