"""Tests for hooks router."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


@pytest.fixture
def client():
    """Create a test client for the API."""
    config = Config(session_api_keys=[])
    app = create_app(config)
    return TestClient(app)


class TestHooksRouter:
    """Tests for hooks router endpoints."""

    def test_get_hooks_success(self, client):
        """Test getting hooks from a valid hooks.json file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"

            hooks_data = {
                "hooks": {
                    "stop": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "echo 'stop hook'"}
                            ],
                        }
                    ]
                }
            }
            hooks_file.write_text(json.dumps(hooks_data))

            response = client.post(
                "/api/hooks",
                json={"project_dir": tmpdir},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["hook_config"] is not None
            assert len(data["hook_config"]["stop"]) == 1

    def test_get_hooks_file_not_found(self, client):
        """Test getting hooks when hooks.json does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            response = client.post(
                "/api/hooks",
                json={"project_dir": tmpdir},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["hook_config"] is None

    def test_get_hooks_no_project_dir(self, client):
        """Test getting hooks with no project_dir provided."""
        response = client.post(
            "/api/hooks",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["hook_config"] is None

    def test_get_hooks_empty_hooks(self, client):
        """Test getting hooks when hooks.json is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with empty content
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"
            hooks_file.write_text("{}")

            response = client.post(
                "/api/hooks",
                json={"project_dir": tmpdir},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["hook_config"] is None

    def test_get_hooks_multiple_event_types(self, client):
        """Test getting hooks with multiple event types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with multiple event types
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"

            hooks_data = {
                "hooks": {
                    "stop": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "echo 'stop'"}],
                        }
                    ],
                    "pre_tool_use": [
                        {
                            "matcher": "terminal",
                            "hooks": [
                                {"type": "command", "command": "echo 'pre_tool_use'"}
                            ],
                        }
                    ],
                }
            }
            hooks_file.write_text(json.dumps(hooks_data))

            response = client.post(
                "/api/hooks",
                json={"project_dir": tmpdir},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["hook_config"] is not None
            assert len(data["hook_config"]["stop"]) == 1
            assert len(data["hook_config"]["pre_tool_use"]) == 1


class TestHooksRouterUserHooks:
    """The route merges user hooks unless load_user_hooks is false."""

    @staticmethod
    def _write(path: Path, command: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "pre_tool_use": [
                        {
                            "matcher": "terminal",
                            "hooks": [{"type": "command", "command": command}],
                        }
                    ]
                }
            )
        )

    def test_merges_user_hooks_by_default(self, client, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        project = tmp_path / "project"
        self._write(project / ".openhands" / "hooks.json", "echo project")
        self._write(user_dir / "hooks.json", "echo user")

        response = client.post("/api/hooks", json={"project_dir": str(project)})

        assert response.status_code == 200
        commands = [
            m["hooks"][0]["command"]
            for m in response.json()["hook_config"]["pre_tool_use"]
        ]
        assert commands == ["echo project", "echo user"]

    def test_load_flags(self, client, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        project = tmp_path / "project"
        self._write(project / ".openhands" / "hooks.json", "echo project")
        self._write(user_dir / "hooks.json", "echo user")

        no_user = client.post(
            "/api/hooks", json={"project_dir": str(project), "load_user_hooks": False}
        ).json()["hook_config"]
        no_project = client.post(
            "/api/hooks",
            json={"project_dir": str(project), "load_project_hooks": False},
        ).json()["hook_config"]

        assert [m["hooks"][0]["command"] for m in no_user["pre_tool_use"]] == [
            "echo project"
        ]
        assert [m["hooks"][0]["command"] for m in no_project["pre_tool_use"]] == [
            "echo user"
        ]
