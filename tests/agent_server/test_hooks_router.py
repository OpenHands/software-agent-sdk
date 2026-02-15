from pathlib import Path

from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


def test_hooks_endpoint_returns_none_when_not_found(tmp_path):
    app = create_app(Config(session_api_keys=[]))
    client = TestClient(app)

    resp = client.post(
        "/api/hooks",
        json={"load_project": True, "load_user": False, "project_dir": str(tmp_path)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["hook_config"] is None


def test_hooks_endpoint_returns_hook_config_when_present(tmp_path):
    hooks_dir = tmp_path / ".openhands"
    hooks_dir.mkdir(parents=True)
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        '{"session_start":[{"matcher":"*","hooks":[{"command":"echo hi"}]}]}'
    )

    app = create_app(Config(session_api_keys=[]))
    client = TestClient(app)

    resp = client.post(
        "/api/hooks",
        json={"load_project": True, "load_user": False, "project_dir": str(tmp_path)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["hook_config"] is not None
    assert data["hook_config"]["session_start"][0]["hooks"][0]["command"] == "echo hi"


def test_hooks_endpoint_respects_load_project_false(tmp_path):
    hooks_dir = tmp_path / ".openhands"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        '{"session_start":[{"matcher":"*","hooks":[{"command":"echo hi"}]}]}'
    )

    app = create_app(Config(session_api_keys=[]))
    client = TestClient(app)

    resp = client.post(
        "/api/hooks",
        json={"load_project": False, "load_user": False, "project_dir": str(tmp_path)},
    )
    assert resp.status_code == 200
    assert resp.json()["hook_config"] is None


def test_hooks_endpoint_accepts_relative_project_dir_and_returns_none(tmp_path):
    app = create_app(Config(session_api_keys=[]))
    client = TestClient(app)

    resp = client.post(
        "/api/hooks",
        json={
            "load_project": True,
            "load_user": False,
            "project_dir": "relative/path",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["hook_config"] is None


def test_hooks_endpoint_returns_none_on_malformed_project_hooks_json(tmp_path):
    hooks_dir = tmp_path / ".openhands"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text("not json")

    app = create_app(Config(session_api_keys=[]))
    client = TestClient(app)

    resp = client.post(
        "/api/hooks",
        json={"load_project": True, "load_user": False, "project_dir": str(tmp_path)},
    )

    assert resp.status_code == 200
    assert resp.json()["hook_config"] is None


def test_hooks_endpoint_merges_project_and_user_hooks(tmp_path, monkeypatch):
    app = create_app(Config(session_api_keys=[]))
    client = TestClient(app)

    hooks_dir = tmp_path / ".openhands"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        '{"session_start":[{"matcher":"*","hooks":[{"command":"echo project"}]}]}'
    )

    fake_home = tmp_path / "fake_home"
    (fake_home / ".openhands").mkdir(parents=True)
    (fake_home / ".openhands" / "hooks.json").write_text(
        '{"session_start":[{"matcher":"*","hooks":[{"command":"echo user"}]}]}'
    )

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    resp = client.post(
        "/api/hooks",
        json={"load_project": True, "load_user": True, "project_dir": str(tmp_path)},
    )

    assert resp.status_code == 200
    hook_config = resp.json()["hook_config"]
    assert hook_config is not None

    session_start = hook_config["session_start"]
    commands = [
        hook["command"] for matcher in session_start for hook in matcher["hooks"]
    ]
    assert commands == ["echo project", "echo user"]
