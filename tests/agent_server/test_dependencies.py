"""
Unit tests for dependency-based authentication functionality.
Tests the check_session_api_key dependency with multiple session API keys support.
"""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.config import Config
from openhands.agent_server.dependencies import check_session_api_key


def _make_app(session_api_keys: list[str]) -> FastAPI:
    app = FastAPI()
    app.state.config = Config(session_api_keys=session_api_keys)

    @app.get("/test", dependencies=[Depends(check_session_api_key)])
    async def test_endpoint():
        return {"message": "success"}

    return app


def test_check_session_api_key_valid():
    client = TestClient(_make_app(["test-key"]), raise_server_exceptions=False)
    assert (
        client.get("/test", headers={"X-Session-API-Key": "test-key"}).status_code
        == 200
    )


def test_check_session_api_key_invalid():
    client = TestClient(_make_app(["test-key"]), raise_server_exceptions=False)
    assert (
        client.get("/test", headers={"X-Session-API-Key": "wrong-key"}).status_code
        == 401
    )


def test_check_session_api_key_missing():
    client = TestClient(_make_app(["test-key"]), raise_server_exceptions=False)
    assert client.get("/test").status_code == 401


def test_check_session_api_key_no_keys_configured():
    """When no keys are configured the endpoint is open."""
    client = TestClient(_make_app([]), raise_server_exceptions=False)
    assert client.get("/test").status_code == 200
    assert (
        client.get("/test", headers={"X-Session-API-Key": "any-key"}).status_code == 200
    )


def test_check_session_api_key_reflects_config_update():
    """Updating app.state.config is reflected immediately; no route re-registration needed."""  # noqa: E501
    app = _make_app(["old-key"])
    client = TestClient(app, raise_server_exceptions=False)

    assert (
        client.get("/test", headers={"X-Session-API-Key": "old-key"}).status_code == 200
    )

    app.state.config = Config(session_api_keys=["new-key"])

    assert (
        client.get("/test", headers={"X-Session-API-Key": "new-key"}).status_code == 200
    )
    assert (
        client.get("/test", headers={"X-Session-API-Key": "old-key"}).status_code == 401
    )
