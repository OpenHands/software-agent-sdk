from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.sdk.llm import llm_profile_store


def _make_client(config: Config) -> TestClient:
    return TestClient(create_app(config))


def test_llm_profile_crud_round_trip(tmp_path: Path, monkeypatch) -> None:
    profile_dir = tmp_path / "profiles"
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)
    client = _make_client(
        Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    )

    payload = {
        "llm": {
            "usage_id": "test-llm",
            "model": "gpt-4o",
            "api_key": "secret-api-key-12345",
        }
    }

    response = client.put("/api/llm-profiles/fast", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "fast"
    assert data["llm"]["model"] == "gpt-4o"
    assert data["llm"]["api_key"] != "secret-api-key-12345"

    profile_path = profile_dir / "fast.json"
    assert profile_path.exists()
    assert "secret-api-key-12345" not in profile_path.read_text()

    response = client.get("/api/llm-profiles/fast")
    assert response.status_code == 200
    assert response.json()["id"] == "fast"

    response = client.get("/api/llm-profiles")
    assert response.status_code == 200
    assert response.json()["profiles"][0]["id"] == "fast"

    response = client.delete("/api/llm-profiles/fast")
    assert response.status_code == 200
    assert response.json()["success"] is True

    response = client.get("/api/llm-profiles")
    assert response.status_code == 200
    assert response.json()["profiles"] == []


def test_put_llm_profile_requires_secret_key_for_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        llm_profile_store, "_DEFAULT_PROFILE_DIR", tmp_path / "profiles"
    )
    client = _make_client(Config(session_api_keys=[], secret_key=None))

    payload = {
        "llm": {
            "usage_id": "test-llm",
            "model": "gpt-4o",
            "api_key": "secret-api-key-12345",
        }
    }

    response = client.put("/api/llm-profiles/fast", json=payload)
    assert response.status_code == 400
    assert "OH_SECRET_KEY" in response.json()["detail"]


def test_put_llm_profile_requires_secret_key_for_aws_session_token(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        llm_profile_store, "_DEFAULT_PROFILE_DIR", tmp_path / "profiles"
    )
    client = _make_client(Config(session_api_keys=[], secret_key=None))

    payload = {
        "llm": {
            "usage_id": "test-llm",
            "model": "gpt-4o",
            "aws_session_token": "temporary-session-token",
        }
    }

    response = client.put("/api/llm-profiles/fast", json=payload)
    assert response.status_code == 400
    assert "OH_SECRET_KEY" in response.json()["detail"]
