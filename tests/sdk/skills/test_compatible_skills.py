"""Tests for discovering AgentSkills from other agents' native directories.

Covers the ``load_compatible_skills`` opt-in: vendor directories
(``.claude/skills``, ``.codex/skills``, ``.gemini/skills``, ``.cursor/skills``)
are additive and lower precedence than the OpenHands / ``.agents`` locations, and
nothing changes when the setting is off.
"""

from pathlib import Path

import pytest

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.skills import (
    Skill,
    load_available_skills,
    load_project_skills,
    skill as skill_module,
)


def _write_skill(root: Path, name: str, body: str = "content") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"---\nname: {name}\ndescription: {name} desc\n---\n{body}\n")
    return skill_md


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    """Isolate ``$HOME`` and the user skill search dirs to ``tmp_path``."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(skill_module, "USER_SKILLS_DIRS", [home / ".agents" / "skills"])
    return home


# -- user scope ---------------------------------------------------------------


def test_user_compatible_skill_loads_when_enabled(user_home):
    _write_skill(user_home / ".claude" / "skills", "claude-only", "from claude")

    disabled = skill_module.load_user_skills()
    assert [s.name for s in disabled] == []

    enabled = skill_module.load_user_skills(include_compatible=True)
    assert [s.name for s in enabled] == ["claude-only"]
    assert enabled[0].content.strip() == "from claude"
    assert enabled[0].source == str(
        (user_home / ".claude" / "skills" / "claude-only" / "SKILL.md").resolve()
    )


def test_user_compatible_covers_every_vendor_dir(user_home):
    for parent in (".claude", ".codex", ".gemini", ".cursor"):
        _write_skill(user_home / parent / "skills", f"{parent[1:]}-skill")

    names = {s.name for s in skill_module.load_user_skills(include_compatible=True)}
    assert names == {"claude-skill", "codex-skill", "gemini-skill", "cursor-skill"}


def test_user_agents_skills_wins_over_vendor(user_home):
    _write_skill(user_home / ".agents" / "skills", "dup", "from agents")
    _write_skill(user_home / ".claude" / "skills", "dup", "from claude")

    skills = skill_module.load_user_skills(include_compatible=True)
    assert len(skills) == 1
    assert skills[0].content.strip() == "from agents"


# -- project scope ------------------------------------------------------------


def test_project_compatible_skill_loads_when_enabled(tmp_path):
    _write_skill(tmp_path / ".claude" / "skills", "claude-only", "from claude")

    assert load_project_skills(tmp_path) == []

    skills = load_project_skills(tmp_path, include_compatible=True)
    assert [s.name for s in skills] == ["claude-only"]
    assert skills[0].content.strip() == "from claude"


def test_project_compatible_covers_every_vendor_dir(tmp_path):
    for parent in (".claude", ".codex", ".gemini", ".cursor"):
        _write_skill(tmp_path / parent / "skills", f"{parent[1:]}-skill")

    names = {s.name for s in load_project_skills(tmp_path, include_compatible=True)}
    assert names == {"claude-skill", "codex-skill", "gemini-skill", "cursor-skill"}


def test_project_agents_skills_wins_over_vendor(tmp_path):
    _write_skill(tmp_path / ".agents" / "skills", "dup", "from agents")
    _write_skill(tmp_path / ".claude" / "skills", "dup", "from claude")

    skills = load_project_skills(tmp_path, include_compatible=True)
    assert len(skills) == 1
    assert skills[0].content.strip() == "from agents"


def test_vendor_dir_symlinked_to_agents_is_not_double_loaded(tmp_path):
    agents_skills = tmp_path / ".agents" / "skills"
    _write_skill(agents_skills, "shared")
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "skills").symlink_to(agents_skills)

    skills = load_project_skills(tmp_path, include_compatible=True)
    assert [s.name for s in skills] == ["shared"]


def test_vendor_skill_loads_from_git_repo_root_when_working_dir_is_subdir(tmp_path):
    (tmp_path / ".git").mkdir()
    _write_skill(tmp_path / ".claude" / "skills", "repo-root-vendor")
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    assert load_project_skills(subdir) == []

    skills = load_project_skills(subdir, include_compatible=True)
    assert [s.name for s in skills] == ["repo-root-vendor"]


# -- merged catalog / setting surface ----------------------------------------


def test_load_available_skills_merges_vendor_with_distinct_source(tmp_path, user_home):
    _write_skill(user_home / ".agents" / "skills", "base")
    _write_skill(user_home / ".claude" / "skills", "vendor")

    disabled = load_available_skills(include_user=True)
    assert set(disabled) == {"base"}

    enabled = load_available_skills(include_user=True, include_compatible=True)
    assert set(enabled) == {"base", "vendor"}
    assert enabled["vendor"].source == str(
        (user_home / ".claude" / "skills" / "vendor" / "SKILL.md").resolve()
    )


def test_agent_context_load_compatible_skills_surfaces_vendor(user_home):
    _write_skill(user_home / ".claude" / "skills", "vendor")

    assert AgentContext().skills == []

    context = AgentContext(load_compatible_skills=True)
    assert [s.name for s in context.skills] == ["vendor"]


def test_agent_context_compatible_project_skills_load_lazily(tmp_path, user_home):
    """A vendor project skill is resolved by LocalConversation when enabled."""
    from openhands.sdk.agent import Agent
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.testing import TestLLM

    _write_skill(tmp_path / ".claude" / "skills", "vendor", "SENTINEL_VENDOR")

    agent = Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="ok")])],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
        agent_context=AgentContext(
            load_compatible_skills=True, current_datetime="2026-01-01T00:00:00Z"
        ),
    )
    conversation = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )
    conversation.send_message("hi")

    context = conversation.agent.agent_context
    assert context is not None
    assert "vendor" in {s.name for s in context.skills}


# -- precedence against other sources ----------------------------------------


def test_installed_skill_wins_over_vendor(user_home, monkeypatch):
    """An installed OpenHands skill must not be displaced by a vendor skill."""
    from openhands.sdk.skills import installed as installed_module

    installed = Skill(
        name="review",
        content="FROM_OPENHANDS",
        description="installed",
        is_agentskills_format=True,
    )
    monkeypatch.setattr(
        installed_module, "load_installed_skills", lambda *a, **k: [installed]
    )
    _write_skill(user_home / ".claude" / "skills", "review", "FROM_CLAUDE")

    skills = skill_module.load_user_skills(include_compatible=True)

    assert [s.name for s in skills] == ["review"]
    assert skills[0].content.strip() == "FROM_OPENHANDS"


def test_root_agents_skill_wins_over_subdir_vendor(tmp_path):
    """A repo-root `.agents` skill beats a vendor skill in a subdirectory."""
    (tmp_path / ".git").mkdir()
    _write_skill(tmp_path / ".agents" / "skills", "build", "FROM_AGENTS")
    subdir = tmp_path / "src"
    subdir.mkdir()
    _write_skill(subdir / ".claude" / "skills", "build", "FROM_CLAUDE")

    skills = load_project_skills(subdir, include_compatible=True)

    assert [s.name for s in skills] == ["build"]
    assert skills[0].content.strip() == "FROM_AGENTS"


def test_work_dir_vendor_wins_over_root_vendor(tmp_path):
    """Within the vendor group, the working directory still precedes the root."""
    (tmp_path / ".git").mkdir()
    _write_skill(tmp_path / ".claude" / "skills", "dup", "FROM_ROOT")
    subdir = tmp_path / "src"
    subdir.mkdir()
    _write_skill(subdir / ".claude" / "skills", "dup", "FROM_SUBDIR")

    skills = load_project_skills(subdir, include_compatible=True)

    assert [s.name for s in skills] == ["dup"]
    assert skills[0].content.strip() == "FROM_SUBDIR"


def test_vendor_loose_markdown_is_not_loaded(tmp_path):
    """Only SKILL.md skills come from vendor dirs; loose notes must not enter
    the permanent system prompt."""
    vendor = tmp_path / ".claude" / "skills"
    _write_skill(vendor, "real-skill", "FROM_SKILL_MD")
    (vendor / "notes.md").write_text("# scratch notes\n")

    skills = load_project_skills(tmp_path, include_compatible=True)

    assert [s.name for s in skills] == ["real-skill"]
