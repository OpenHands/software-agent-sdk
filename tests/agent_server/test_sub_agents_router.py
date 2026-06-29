"""Tests for the sub-agents router (POST /sub-agents)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    config = Config(session_api_keys=[])  # Disable authentication
    return TestClient(create_app(config), raise_server_exceptions=False)


def _write_agent(directory: Path, name: str, description: str = "desc") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nPrompt for {name}."
    )


def test_get_sub_agents_returns_builtins_only(client):
    """Built-ins are listed and tagged; user/project sources can be excluded."""
    response = client.post(
        "/api/sub-agents",
        json={"load_builtin": True, "load_user": False, "load_project": False},
    )
    assert response.status_code == 200
    agents = response.json()["agents"]
    names = {a["name"] for a in agents}

    assert {"general-purpose", "code-explorer", "bash-runner"} <= names
    assert all(a["level"] == "builtin" and a["is_builtin"] for a in agents)
    # system_prompt rides inline so a detail view needs no extra fetch
    assert all(a["system_prompt"] for a in agents)


def test_get_sub_agents_includes_project_and_shadows_builtin(client, tmp_path: Path):
    """A project agent appears and shadows a built-in with the same name."""
    _write_agent(tmp_path / ".agents" / "agents", "code-reviewer", "Reviews code")
    # Same name as a built-in -> project definition must win.
    _write_agent(tmp_path / ".agents" / "agents", "general-purpose", "Overridden")

    response = client.post(
        "/api/sub-agents",
        json={
            "load_builtin": True,
            "load_user": False,
            "load_project": True,
            "project_dir": str(tmp_path),
        },
    )
    assert response.status_code == 200
    agents = {a["name"]: a for a in response.json()["agents"]}

    assert agents["code-reviewer"]["level"] == "project"
    assert agents["code-reviewer"]["is_builtin"] is False

    # Only one general-purpose entry, and it is the project (shadowing) one.
    gp = agents["general-purpose"]
    assert gp["level"] == "project"
    assert gp["is_builtin"] is False
    assert gp["description"] == "Overridden"


def test_get_sub_agents_load_builtin_false(client):
    """Disabling built-ins with no other source yields an empty catalog."""
    response = client.post(
        "/api/sub-agents",
        json={"load_builtin": False, "load_user": False, "load_project": False},
    )
    assert response.status_code == 200
    assert response.json()["agents"] == []


def test_get_sub_agents_exposes_full_frontmatter(client, tmp_path: Path):
    """Every frontmatter-settable field is returned (lossless view)."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "full.md").write_text(
        "---\n"
        "name: full\n"
        "description: Fully specified agent\n"
        "model: inherit\n"
        "color: purple\n"
        "tools:\n  - TerminalTool\n"
        "skills:\n  - some-skill\n"
        "permission_mode: confirm_risky\n"
        "max_iteration_per_run: 7\n"
        "max_budget_per_run: 1.5\n"
        "profile_store_dir: /tmp/profiles\n"
        "mcp_servers:\n"
        "  fetch:\n    command: uvx\n    args:\n      - mcp-server-fetch\n"
        "condenser: none\n"
        "custom_key: custom_value\n"
        "---\n\n"
        "You are a fully specified agent."
    )

    response = client.post(
        "/api/sub-agents",
        json={"load_builtin": False, "load_user": False, "project_dir": str(tmp_path)},
    )
    assert response.status_code == 200
    agent = {a["name"]: a for a in response.json()["agents"]}["full"]

    assert agent["skills"] == ["some-skill"]
    assert agent["permission_mode"] == "confirm_risky"
    assert agent["max_iteration_per_run"] == 7
    assert agent["max_budget_per_run"] == 1.5
    assert agent["profile_store_dir"] == "/tmp/profiles"
    assert agent["mcp_servers"] == {
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
    }
    # condenser: none -> a NoOpCondenser is serialized (not null)
    assert agent["condenser"] is not None
    # unknown frontmatter keys are preserved in metadata
    assert agent["metadata"]["custom_key"] == "custom_value"
