"""Tests for RequestValidationError sanitization in the agent server.

Verifies that 422 error responses do not leak sensitive fields such as
``api_key``, ``acp_env``, or other secret-bearing request values.

Refs: OpenHands/evaluation#385
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from openhands.agent_server.api import (
    _add_exception_handlers,
    _sanitize_validation_errors,
    create_app,
)
from openhands.agent_server.config import Config


# ---------------------------------------------------------------------------
# Unit tests for _sanitize_validation_errors
# ---------------------------------------------------------------------------


class TestSanitizeValidationErrors:
    """Unit tests for _sanitize_validation_errors helper."""

    def test_redacts_api_key_in_input(self):
        """api_key values inside the input dict should be redacted."""
        errors = [
            {
                "type": "missing",
                "loc": ["body", "agent", "tools"],
                "msg": "Field required",
                "input": {
                    "agent": {
                        "llm": {
                            "model": "gpt-4",
                            "api_key": "sk-real-secret-key-12345",
                        },
                        "tools": [],
                    },
                    "workspace": {"working_dir": "/tmp"},
                },
            }
        ]
        result = _sanitize_validation_errors(errors)
        assert len(result) == 1
        agent_input = result[0]["input"]["agent"]
        assert agent_input["llm"]["api_key"] == "<redacted>"
        # Non-secret fields should be preserved
        assert agent_input["llm"]["model"] == "gpt-4"

    def test_redacts_acp_env_values(self):
        """All values under acp_env should be fully redacted."""
        errors = [
            {
                "type": "value_error",
                "loc": ["body"],
                "msg": "Invalid value",
                "input": {
                    "agent": {
                        "acp_env": {
                            "OPENAI_API_KEY": "sk-secret",
                            "DATABASE_URL": "postgres://user:pass@host/db",
                        },
                    },
                },
            }
        ]
        result = _sanitize_validation_errors(errors)
        acp_env = result[0]["input"]["agent"]["acp_env"]
        assert acp_env["OPENAI_API_KEY"] == "<redacted>"
        assert acp_env["DATABASE_URL"] == "<redacted>"

    def test_preserves_non_secret_fields(self):
        """Non-secret fields should pass through unchanged."""
        errors = [
            {
                "type": "missing",
                "loc": ["body", "workspace"],
                "msg": "Field required",
                "input": {
                    "agent": {
                        "llm": {"model": "claude-3"},
                        "tools": [{"name": "bash"}],
                    },
                },
            }
        ]
        result = _sanitize_validation_errors(errors)
        assert result[0]["input"]["agent"]["llm"]["model"] == "claude-3"
        assert result[0]["input"]["agent"]["tools"] == [{"name": "bash"}]

    def test_handles_errors_without_input(self):
        """Errors that lack an 'input' key should pass through unchanged."""
        errors = [
            {
                "type": "missing",
                "loc": ["body"],
                "msg": "Field required",
            }
        ]
        result = _sanitize_validation_errors(errors)
        assert result == errors

    def test_handles_scalar_input(self):
        """Scalar input values should pass through unchanged."""
        errors = [
            {
                "type": "type_error",
                "loc": ["body", "max_iterations"],
                "msg": "value is not a valid integer",
                "input": "not_a_number",
            }
        ]
        result = _sanitize_validation_errors(errors)
        assert result[0]["input"] == "not_a_number"

    def test_does_not_mutate_original(self):
        """The original error list should not be modified."""
        original_errors = [
            {
                "type": "missing",
                "loc": ["body"],
                "msg": "Field required",
                "input": {
                    "agent": {
                        "llm": {"api_key": "sk-secret"},
                    },
                },
            }
        ]
        # Keep a reference to the original input
        original_api_key = original_errors[0]["input"]["agent"]["llm"]["api_key"]
        _sanitize_validation_errors(original_errors)
        # Original should be untouched
        assert original_errors[0]["input"]["agent"]["llm"]["api_key"] == original_api_key

    def test_redacts_multiple_secret_patterns(self):
        """Various secret key patterns should all be redacted."""
        errors = [
            {
                "type": "value_error",
                "loc": ["body"],
                "msg": "Invalid",
                "input": {
                    "api_key": "secret1",
                    "api_token": "secret2",
                    "password": "secret3",
                    "authorization": "Bearer secret4",
                    "x_session_id": "secret5",
                    "name": "safe_value",
                },
            }
        ]
        result = _sanitize_validation_errors(errors)
        inp = result[0]["input"]
        assert inp["api_key"] == "<redacted>"
        assert inp["api_token"] == "<redacted>"
        assert inp["password"] == "<redacted>"
        assert inp["authorization"] == "<redacted>"
        assert inp["x_session_id"] == "<redacted>"
        assert inp["name"] == "safe_value"

    def test_empty_errors_list(self):
        """An empty error list should return an empty list."""
        assert _sanitize_validation_errors([]) == []


# ---------------------------------------------------------------------------
# Integration tests using a real FastAPI test client
# ---------------------------------------------------------------------------


class TestValidationErrorResponse:
    """Integration tests verifying 422 responses are sanitized end-to-end."""

    @pytest.fixture
    def app_with_validation(self):
        """Create a minimal FastAPI app with our exception handlers and a
        route that will trigger a RequestValidationError."""
        app = FastAPI()
        _add_exception_handlers(app)

        class SecretPayload(BaseModel):
            name: str
            api_key: str
            acp_env: dict[str, str] = {}

        @app.post("/test-endpoint")
        async def test_endpoint(payload: SecretPayload):
            return {"ok": True}

        return app

    def test_422_response_redacts_api_key(self, app_with_validation):
        """Sending a payload that fails validation should not leak api_key."""
        client = TestClient(app_with_validation)
        # Send a payload missing the required 'name' field but with api_key
        response = client.post(
            "/test-endpoint",
            json={
                "api_key": "sk-super-secret-key",
                "acp_env": {"PROVIDER_KEY": "provider-secret"},
            },
        )
        assert response.status_code == 422
        body = response.json()

        # Verify the response has the expected structure
        assert "detail" in body
        assert len(body["detail"]) > 0

        # Check that secrets are redacted in the input
        for error in body["detail"]:
            if "input" in error and isinstance(error["input"], dict):
                if "api_key" in error["input"]:
                    assert error["input"]["api_key"] == "<redacted>"
                if "acp_env" in error["input"]:
                    for val in error["input"]["acp_env"].values():
                        assert val == "<redacted>"

    def test_422_response_preserves_error_structure(self, app_with_validation):
        """The sanitized 422 should preserve error type, loc, and msg."""
        client = TestClient(app_with_validation)
        response = client.post(
            "/test-endpoint",
            json={"api_key": "sk-secret"},
        )
        assert response.status_code == 422
        body = response.json()

        # Verify standard FastAPI validation error structure
        assert "detail" in body
        for error in body["detail"]:
            assert "type" in error
            assert "loc" in error
            assert "msg" in error

    def test_valid_request_unaffected(self, app_with_validation):
        """Valid requests should not be affected by the exception handler."""
        client = TestClient(app_with_validation)
        response = client.post(
            "/test-endpoint",
            json={
                "name": "test",
                "api_key": "sk-key",
                "acp_env": {},
            },
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_422_with_non_json_body(self, app_with_validation):
        """Sending non-JSON body should still return sanitized 422."""
        client = TestClient(app_with_validation)
        response = client.post(
            "/test-endpoint",
            content="this is not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
