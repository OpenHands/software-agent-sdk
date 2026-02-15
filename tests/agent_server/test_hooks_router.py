from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


def test_hooks_endpoint_returns_none_when_not_found(tmp_path):
    app = create_app(Config(session_api_keys=[]))
    client = TestClient(app)

    resp = client.post("/api/hooks", json={"project_dir": str(tmp_path)})
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

    resp = client.post("/api/hooks", json={"project_dir": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["hook_config"] is not None
    assert data["hook_config"]["session_start"][0]["hooks"][0]["command"] == "echo hi"
