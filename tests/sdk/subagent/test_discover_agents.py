"""Tests for non-mutating agent discovery (``discover_agents``)."""

from pathlib import Path
from unittest.mock import patch

from openhands.sdk.subagent.load import discover_agents
from openhands.sdk.subagent.registry import (
    _agent_factories,
    _reset_registry_for_tests,
)


def setup_function() -> None:
    _reset_registry_for_tests()


def teardown_function() -> None:
    _reset_registry_for_tests()


def _write_agent(directory: Path, name: str, description: str = "desc") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nPrompt for {name}."
    )


def test_discover_agents_sets_level_and_source(tmp_path: Path) -> None:
    """Discovered project/user agents carry level + source."""
    _write_agent(tmp_path / ".agents" / "agents", "project-agent")
    home = tmp_path / "home"
    _write_agent(home / ".agents" / "agents", "user-agent")

    with patch("openhands.sdk.subagent.load.Path.home", return_value=home):
        agents = discover_agents(tmp_path)

    by_name = {a.name: a for a in agents}
    assert by_name["project-agent"].level == "project"
    assert by_name["user-agent"].level == "user"
    assert by_name["project-agent"].source is not None
    assert by_name["project-agent"].system_prompt == "Prompt for project-agent."


def test_discover_agents_project_wins_over_user(tmp_path: Path) -> None:
    """Project agent shadows a user agent with the same name."""
    _write_agent(tmp_path / ".agents" / "agents", "shared", description="from project")
    home = tmp_path / "home"
    _write_agent(home / ".agents" / "agents", "shared", description="from user")

    with patch("openhands.sdk.subagent.load.Path.home", return_value=home):
        agents = discover_agents(tmp_path)

    shared = [a for a in agents if a.name == "shared"]
    assert len(shared) == 1
    assert shared[0].level == "project"
    assert shared[0].description == "from project"


def test_discover_agents_does_not_mutate_registry(tmp_path: Path) -> None:
    """Discovery is read-only: the global registry stays untouched."""
    _write_agent(tmp_path / ".agents" / "agents", "project-agent")

    with patch("openhands.sdk.subagent.load.Path.home", return_value=tmp_path / "h"):
        agents = discover_agents(tmp_path, include_user=False)

    assert [a.name for a in agents] == ["project-agent"]
    assert _agent_factories == {}


def test_discover_agents_respects_include_flags(tmp_path: Path) -> None:
    """include_project / include_user gate their respective sources."""
    _write_agent(tmp_path / ".agents" / "agents", "project-agent")
    home = tmp_path / "home"
    _write_agent(home / ".agents" / "agents", "user-agent")

    with patch("openhands.sdk.subagent.load.Path.home", return_value=home):
        only_user = discover_agents(tmp_path, include_project=False)
        only_project = discover_agents(tmp_path, include_user=False)

    assert [a.name for a in only_user] == ["user-agent"]
    assert [a.name for a in only_project] == ["project-agent"]


def test_discover_agents_none_project_dir(tmp_path: Path) -> None:
    """A None project_dir skips project discovery without error."""
    home = tmp_path / "home"
    _write_agent(home / ".agents" / "agents", "user-agent")

    with patch("openhands.sdk.subagent.load.Path.home", return_value=home):
        agents = discover_agents(None)

    assert [a.name for a in agents] == ["user-agent"]
