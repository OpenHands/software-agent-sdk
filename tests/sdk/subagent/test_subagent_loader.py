"""Tests for file-based agent loading and registration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from openhands.sdk.subagent.load import (
    load_project_agents,
    load_user_agents,
)
from openhands.sdk.subagent.registration import (
    _reset_registry_for_tests,
)


def setup_function() -> None:
    _reset_registry_for_tests()


def teardown_function() -> None:
    _reset_registry_for_tests()


def test_load_project_agents(tmp_path: Path) -> None:
    """Loads .md files from .agents/ root directory."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir()

    (agents_dir / "code-reviewer.md").write_text(
        "---\n"
        "name: code-reviewer\n"
        "description: Reviews code\n"
        "tools:\n"
        "  - ReadTool\n"
        "---\n\n"
        "You are a code reviewer."
    )
    (agents_dir / "security-expert.md").write_text(
        "---\n"
        "name: security-expert\n"
        "description: Security analysis\n"
        "---\n\n"
        "You are a security expert."
    )

    agents = load_project_agents(tmp_path)
    names = {a.name for a in agents}
    assert names == {"code-reviewer", "security-expert"}

    # Verify the code-reviewer was parsed correctly
    reviewer = next(a for a in agents if a.name == "code-reviewer")
    assert reviewer.description == "Reviews code"
    assert "ReadTool" in reviewer.tools
    assert reviewer.system_prompt == "You are a code reviewer."


def test_load_project_agents_skips_subdirs(tmp_path: Path) -> None:
    """Does not recurse into subdirectories like skills/."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir()

    # Top-level agent
    (agents_dir / "top-agent.md").write_text(
        "---\nname: top-agent\ndescription: Top\n---\nPrompt."
    )

    # Subdirectory (should be skipped)
    skills_dir = agents_dir / "skills"
    skills_dir.mkdir()
    (skills_dir / "nested-agent.md").write_text(
        "---\nname: nested-agent\ndescription: Nested\n---\nPrompt."
    )

    agents = load_project_agents(tmp_path)
    names = {a.name for a in agents}
    assert names == {"top-agent"}
    assert "nested-agent" not in names


def test_load_project_agents_empty(tmp_path: Path) -> None:
    """Returns [] for missing .agents/ directory."""
    agents = load_project_agents(tmp_path)
    assert agents == []


def test_load_project_agents_skips_readme(tmp_path: Path) -> None:
    """README.md is skipped."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir()

    (agents_dir / "README.md").write_text("# Agents directory")
    (agents_dir / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: Real\n---\nPrompt."
    )

    agents = load_project_agents(tmp_path)
    names = {a.name for a in agents}
    assert names == {"real-agent"}


def test_load_project_agents_allowed_commands(tmp_path: Path) -> None:
    """Parses allowed_commands from frontmatter."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()

    (agents_dir / "runner.md").write_text(
        "---\n"
        "name: runner\n"
        "description: Runs commands\n"
        "allowed-commands:\n"
        "  - pytest\n"
        "  - make\n"
        "---\n\n"
        "You run commands."
    )

    agents = load_project_agents(tmp_path)
    assert len(agents) == 1
    assert agents[0].allowed_commands == ["pytest", "make"]


def test_load_project_agents_from_openhands_dir(tmp_path: Path) -> None:
    """Loads .md files from .openhands/ when .agents/ does not exist."""
    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir()

    (oh_dir / "legacy-agent.md").write_text(
        "---\nname: legacy-agent\ndescription: Legacy\n---\nLegacy prompt."
    )

    agents = load_project_agents(tmp_path)
    assert len(agents) == 1
    assert agents[0].name == "legacy-agent"


def test_load_project_agents_agents_dir_wins_over_openhands(tmp_path: Path) -> None:
    """.agents/ takes precedence over .openhands/ for duplicate names."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir()
    (agents_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .agents\n---\nAgents prompt."
    )

    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir()
    (oh_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .openhands\n---\nOH prompt."
    )
    # Also put a unique agent in .openhands/ to verify it still loads
    (oh_dir / "only-in-oh.md").write_text(
        "---\nname: only-in-oh\ndescription: OH only\n---\nOH only prompt."
    )

    agents = load_project_agents(tmp_path)
    names = [a.name for a in agents]
    assert sorted(names) == ["shared", "only-in-oh"]

    # .agents/ version should win for the duplicate
    shared = next(a for a in agents if a.name == "shared")
    assert shared.description == "From .agents"


def test_load_project_agents_merges_both_dirs(tmp_path: Path) -> None:
    """Agents from both .agents/ and .openhands/ are merged."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "agent-a.md").write_text(
        "---\nname: agent-a\ndescription: A\n---\nA."
    )

    oh_dir = tmp_path / ".openhands"
    oh_dir.mkdir()
    (oh_dir / "agent-b.md").write_text("---\nname: agent-b\ndescription: B\n---\nB.")

    agents = load_project_agents(tmp_path)
    names = {a.name for a in agents}
    assert names == {"agent-a", "agent-b"}


def test_load_user_agents(tmp_path: Path) -> None:
    """Loads from ~/.agents/ directory."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir()

    (agents_dir / "global-agent.md").write_text(
        "---\nname: global-agent\ndescription: Global\n---\nGlobal prompt."
    )

    with patch("openhands.sdk.subagent.load.Path.home", return_value=tmp_path):
        agents = load_user_agents()

    assert len(agents) == 1
    assert agents[0].name == "global-agent"


def test_load_user_agents_from_openhands_dir(tmp_path: Path) -> None:
    """Loads from ~/.openhands/ when ~/.agents/ does not exist."""
    oh_dir = tmp_path / ".openhands"
    oh_dir.mkdir()

    (oh_dir / "legacy-user.md").write_text(
        "---\nname: legacy-user\ndescription: Legacy user\n---\nLegacy."
    )

    with patch("openhands.sdk.subagent.load.Path.home", return_value=tmp_path):
        agents = load_user_agents()

    assert len(agents) == 1
    assert agents[0].name == "legacy-user"


def test_load_user_agents_agents_dir_wins_over_openhands(tmp_path: Path) -> None:
    """~/.agents/ takes precedence over ~/.openhands/ for duplicate names."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir()
    (agents_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .agents\n---\nAgents."
    )

    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir()
    (oh_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .openhands\n---\nOH."
    )

    with patch("openhands.sdk.subagent.load.Path.home", return_value=tmp_path):
        agents = load_user_agents()

    assert len(agents) == 1
    assert agents[0].name == "shared"
    assert agents[0].description == "From .agents"
