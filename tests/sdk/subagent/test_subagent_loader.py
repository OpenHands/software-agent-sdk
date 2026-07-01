"""Tests for file-based agent loading."""

from pathlib import Path
from unittest.mock import patch

from openhands.sdk.subagent.load import (
    load_project_agents,
    load_project_root_agents,
    load_user_agents,
)
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
)


def setup_function() -> None:
    _reset_registry_for_tests()


def teardown_function() -> None:
    _reset_registry_for_tests()


def test_load_project_agents(tmp_path: Path) -> None:
    """Loads .md files from .agents/ root directory."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)

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
    agents_dir.mkdir(parents=True)

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
    agents_dir.mkdir(parents=True)

    (agents_dir / "README.md").write_text("# Agents directory")
    (agents_dir / "readme.md").write_text("# Agents directory")
    (agents_dir / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: Real\n---\nPrompt."
    )

    agents = load_project_agents(tmp_path)
    names = [a.name for a in agents]
    assert names == ["real-agent"]


def test_load_project_agents_from_openhands_dir(tmp_path: Path) -> None:
    """Loads .md files from .openhands/ when .agents/ does not exist."""
    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir(parents=True)

    (oh_dir / "legacy-agent.md").write_text(
        "---\nname: legacy-agent\ndescription: Legacy\n---\nLegacy prompt."
    )

    agents = load_project_agents(tmp_path)
    assert len(agents) == 1
    assert agents[0].name == "legacy-agent"


def test_load_project_agents_agents_dir_wins_over_openhands(tmp_path: Path) -> None:
    """.agents/ takes precedence over .openhands/ for duplicate names."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .agents\n---\nAgents prompt."
    )

    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir(parents=True)
    (oh_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .openhands\n---\nOH prompt."
    )
    # Also put a unique agent in .openhands/ to verify it still loads
    (oh_dir / "only-in-oh.md").write_text(
        "---\nname: only-in-oh\ndescription: OH only\n---\nOH only prompt."
    )

    agents = load_project_agents(tmp_path)
    names = [a.name for a in agents]
    assert sorted(names) == ["only-in-oh", "shared"]

    # .agents/ version should win for the duplicate
    # i.e., the first agent should come from .agents
    assert agents[0].description == "From .agents"


def test_load_project_agents_merges_both_dirs(tmp_path: Path) -> None:
    """Agents from both .agents/ and .openhands/ are merged."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent-a.md").write_text(
        "---\nname: agent-a\ndescription: A\n---\nA."
    )

    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir(parents=True)
    (oh_dir / "agent-b.md").write_text("---\nname: agent-b\ndescription: B\n---\nB.")

    agents = load_project_agents(tmp_path)
    names = [a.name for a in agents]
    assert sorted(names) == ["agent-a", "agent-b"]


def test_load_user_agents(tmp_path: Path) -> None:
    """Loads from ~/.agents/ directory."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "global-agent.md").write_text(
        "---\nname: global-agent\ndescription: Global\n---\nGlobal prompt."
    )

    with patch("openhands.sdk.subagent.load.Path.home", return_value=tmp_path):
        agents = load_user_agents()

    assert len(agents) == 1
    assert agents[0].name == "global-agent"


def test_load_user_agents_from_openhands_dir(tmp_path: Path) -> None:
    """Loads from ~/.openhands/ when ~/.agents/ does not exist."""
    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir(parents=True)

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
    agents_dir.mkdir(parents=True)
    (agents_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .agents\n---\nAgents."
    )

    oh_dir = tmp_path / ".openhands" / "agents"
    oh_dir.mkdir(parents=True)
    (oh_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: From .openhands\n---\nOH."
    )

    with patch("openhands.sdk.subagent.load.Path.home", return_value=tmp_path):
        agents = load_user_agents()

    assert len(agents) == 1
    assert agents[0].name == "shared"
    assert agents[0].description == "From .agents"


def test_load_project_root_agents_splits_agents_md(tmp_path: Path) -> None:
    """AGENTS.md with ## sections produces one AgentDefinition per section."""
    (tmp_path / "AGENTS.md").write_text(
        "## Docker Setup\nUse docker-compose for all services.\n\n"
        "## Building and Testing\nRun ./gradlew test to build and test.\n"
    )
    agents = load_project_root_agents(tmp_path)
    names = {a.name for a in agents}
    assert "sca_docker_setup" in names
    assert "sca_building_and_testing" in names


def test_load_project_root_agents_lowercase_filename(tmp_path: Path) -> None:
    """agents.md (lowercase) is also detected."""
    (tmp_path / "agents.md").write_text(
        "## API Conventions\nAll endpoints return JSON.\n"
    )
    agents = load_project_root_agents(tmp_path)
    assert any(a.name == "sca_api_conventions" for a in agents)


def test_load_project_root_agents_no_file_returns_empty(tmp_path: Path) -> None:
    """Returns [] when no AGENTS.md-style file exists."""
    assert load_project_root_agents(tmp_path) == []


def test_load_project_root_agents_no_sections_fallback(tmp_path: Path) -> None:
    """File with no ## headers falls back to single AgentDefinition load."""
    (tmp_path / "AGENTS.md").write_text(
        "---\nname: monolith-agent\ndescription: A monolith\n---\n\nSome instructions.\n"
    )
    agents = load_project_root_agents(tmp_path)
    assert len(agents) == 1
    assert agents[0].name == "monolith-agent"


def test_load_project_root_agents_triggers_populated(tmp_path: Path) -> None:
    """Returned AgentDefinitions have non-empty triggers."""
    (tmp_path / "AGENTS.md").write_text(
        "## Docker Setup\nUse docker-compose for all services.\n"
    )
    agents = load_project_root_agents(tmp_path)
    assert len(agents) == 1
    assert len(agents[0].triggers) > 0


def test_load_project_root_agents_cursorrules(tmp_path: Path) -> None:
    """.cursorrules with ## headers is parsed as sections."""
    (tmp_path / ".cursorrules").write_text(
        "## Code Style\nUse 4-space indentation.\n\n"
        "## Testing\nWrite tests for all new functions.\n"
    )
    agents = load_project_root_agents(tmp_path)
    names = {a.name for a in agents}
    assert "sca_code_style" in names
    assert "sca_testing" in names
