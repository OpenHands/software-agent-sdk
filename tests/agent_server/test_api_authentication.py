"""
Integration tests for API authentication using dependency-based authentication.
Tests the complete authentication flow through the FastAPI application.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from openhands.agent_server.api import _find_http_exception, create_app
from openhands.agent_server.config import Config


@pytest.fixture
def client():
    """Create a test client for the API without authentication."""
    return TestClient(create_app())


@pytest.fixture
def client_with_auth():
    """Create a test client with session API key authentication."""
    config = Config(session_api_keys=["test-key-123"])
    app = create_app(config)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_with_multiple_keys():
    """Create a test client with multiple session API keys."""
    config = Config(session_api_keys=["key-1", "key-2", "key-3"])
    app = create_app(config)
    return TestClient(app, raise_server_exceptions=False)


def test_find_http_exception():
    """Test the helper function for finding HTTPExceptions in ExceptionGroups."""
    # Test with single HTTPException
    http_exc = HTTPException(status_code=401, detail="Unauthorized")
    exc_group = BaseExceptionGroup("test", [http_exc])

    found = _find_http_exception(exc_group)
    assert found is http_exc

    # Test with multiple exceptions, HTTPException first
    other_exc = ValueError("Some error")
    exc_group = BaseExceptionGroup("test", [http_exc, other_exc])

    found = _find_http_exception(exc_group)
    assert found is http_exc

    # Test with no HTTPException
    exc_group = BaseExceptionGroup("test", [other_exc])

    found = _find_http_exception(exc_group)
    assert found is None

    # Test with nested ExceptionGroup
    nested_group = BaseExceptionGroup("nested", [http_exc])
    outer_group = BaseExceptionGroup("outer", [other_exc, nested_group])

    found = _find_http_exception(outer_group)
    assert found is http_exc


def test_api_no_auth_required(client):
    """Test that API works without authentication when no keys are configured."""
    # Test server details endpoint (should always be accessible)
    response = client.get("/server_info")
    # This might return 404 if endpoint doesn't exist, but should not be 401
    assert response.status_code != 401


def test_api_auth_missing_key(client_with_auth):
    """Integration test: missing X-Session-API-Key should return 401."""
    response = client_with_auth.get("/api/conversations")
    assert response.status_code == 401


def test_api_auth_invalid_key(client_with_auth):
    """Integration test: invalid X-Session-API-Key should return 401."""
    response = client_with_auth.get(
        "/api/conversations", headers={"X-Session-API-Key": "wrong-key"}
    )
    assert response.status_code == 401


def test_api_auth_valid_key(client_with_auth):
    """Integration test: valid X-Session-API-Key should allow access."""
    response = client_with_auth.get(
        "/api/conversations", headers={"X-Session-API-Key": "test-key-123"}
    )
    # Should not be 401 (might be other status depending on endpoint implementation)
    assert response.status_code != 401


def test_api_auth_multiple_keys_all_valid(client_with_multiple_keys):
    """Integration test: all configured keys should work."""
    for key in ["key-1", "key-2", "key-3"]:
        response = client_with_multiple_keys.get(
            "/api/conversations", headers={"X-Session-API-Key": key}
        )
        assert response.status_code != 401, f"Key {key} should be valid"


def test_api_auth_multiple_keys_invalid(client_with_multiple_keys):
    """Integration test: invalid key should fail with multiple keys configured."""
    response = client_with_multiple_keys.get(
        "/api/conversations", headers={"X-Session-API-Key": "invalid-key"}
    )
    assert response.status_code == 401


def test_api_server_details_no_auth_required(client_with_auth):
    """Integration test: server details endpoints should not require authentication."""
    # Server info endpoint should be accessible without auth
    response = client_with_auth.get("/server_info")
    assert response.status_code != 401


def test_api_protected_endpoints_require_auth(client_with_auth):
    """Test that API endpoints under /api prefix require authentication."""
    protected_endpoints = [
        "/api/conversations",
        "/api/tools/",
        "/api/file/download/test.txt",
    ]

    for endpoint in protected_endpoints:
        # Without auth header
        response = client_with_auth.get(endpoint)
        assert response.status_code == 401, f"Endpoint {endpoint} should require auth"

        # With valid auth header
        response = client_with_auth.get(
            endpoint, headers={"X-Session-API-Key": "test-key-123"}
        )
        assert response.status_code != 401, (
            f"Endpoint {endpoint} should accept valid auth"
        )


def test_api_case_sensitive_keys(client_with_auth):
    """Test that API key matching is case-sensitive."""
    # Create client with mixed-case key
    config = Config(session_api_keys=["Test-Key-123"])
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)

    # Exact match should work
    response = client.get(
        "/api/conversations", headers={"X-Session-API-Key": "Test-Key-123"}
    )
    assert response.status_code != 401

    # Case mismatch should fail
    response = client.get(
        "/api/conversations", headers={"X-Session-API-Key": "test-key-123"}
    )
    assert response.status_code == 401


def test_api_header_case_insensitive():
    """Test that HTTP header names are case-insensitive."""
    config = Config(session_api_keys=["test-key"])
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)

    header_variations = [
        "X-Session-API-Key",
        "x-session-api-key",
        "X-SESSION-API-KEY",
        "x-Session-Api-Key",
    ]

    for header_name in header_variations:
        response = client.get("/api/conversations", headers={header_name: "test-key"})
        assert response.status_code != 401, f"Header {header_name} should work"


def test_api_special_character_keys():
    """Test API keys with special characters."""
    special_keys = [
        "key-with-dashes",
        "key_with_underscores",
        "key.with.dots",
        "key@with#special$chars",
    ]

    config = Config(session_api_keys=special_keys)
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)

    for key in special_keys:
        response = client.get("/api/conversations", headers={"X-Session-API-Key": key})
        assert response.status_code != 401, f"Special key {key} should work"


def test_api_empty_key_list():
    """Test that empty session_api_keys list disables authentication."""
    config = Config(session_api_keys=[])
    app = create_app(config)
    client = TestClient(app)

    # Should work without any authentication
    response = client.get("/api/conversations")
    assert response.status_code != 401


def test_api_websocket_authentication():
    """Test that WebSocket connections also respect authentication."""
    config = Config(session_api_keys=["test-key"])
    app = create_app(config)
    client = TestClient(app)

    # Without authentication -> should fail
    with pytest.raises(Exception):
        with client.websocket_connect("/sockets/bash-events"):
            assert False, "WebSocket connection should have failed without auth"

    # Query-param authentication -> should work (browser-compatible)
    with client.websocket_connect("/sockets/bash-events?session_api_key=test-key"):
        pass

    # Header authentication -> should work for non-browser clients
    with client.websocket_connect(
        "/sockets/bash-events",
        headers={"X-Session-API-Key": "test-key"},
    ):
        pass

    # Query param should take precedence over headers (browser-compatible escape hatch).
    with client.websocket_connect(
        "/sockets/bash-events?session_api_key=test-key",
        headers={"X-Session-API-Key": "wrong-key"},
    ):
        pass

    # If query param is present and wrong, connection should fail even if the
    # header is correct.
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/sockets/bash-events?session_api_key=wrong-key",
            headers={"X-Session-API-Key": "test-key"},
        ):
            assert False, "WebSocket connection should have failed with wrong query key"

    # Wrong header -> should fail
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/sockets/bash-events",
            headers={"X-Session-API-Key": "wrong-key"},
        ):
            assert False, "WebSocket connection should have failed with wrong key"


def test_api_websocket_no_auth_required():
    """Test that WebSocket connections work when auth is disabled."""
    config = Config(session_api_keys=[])
    app = create_app(config)
    client = TestClient(app)

    with client.websocket_connect("/sockets/bash-events"):
        pass


def test_api_options_requests():
    """Test that OPTIONS requests work for CORS preflight."""
    config = Config(session_api_keys=["test-key"])
    app = create_app(config)
    client = TestClient(app)

    # OPTIONS requests should work without authentication for CORS
    response = client.options("/api/conversations")
    # Should not be 401, might be 405 (Method Not Allowed) or 200
    assert response.status_code != 401


def test_api_dependency_injection_openapi():
    """Test that the dependency appears in OpenAPI documentation."""
    config = Config(session_api_keys=["test-key"])
    app = create_app(config)
    client = TestClient(app)

    # Get OpenAPI schema
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_schema = response.json()

    # Check that security is defined in the schema
    # The exact structure depends on how FastAPI generates the schema
    # This is a basic check that the schema is generated successfully
    assert "openapi" in openapi_schema
    assert "paths" in openapi_schema


def test_api_multiple_concurrent_requests():
    """Test that multiple concurrent requests with different keys work correctly."""
    config = Config(session_api_keys=["key-1", "key-2"])
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)

    # Simulate concurrent requests with different keys
    responses = []

    for key in ["key-1", "key-2", "invalid-key"]:
        response = client.get("/api/conversations", headers={"X-Session-API-Key": key})
        responses.append((key, response.status_code))

    # Valid keys should work
    assert responses[0][1] != 401  # key-1
    assert responses[1][1] != 401  # key-2

    # Invalid key should fail
    assert responses[2][1] == 401  # invalid-key


def test_api_error_response_format():
    """Test that authentication errors return proper HTTP 401 status."""
    config = Config(session_api_keys=["test-key"])
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/conversations")
    assert response.status_code == 401

    # The response might have additional details, but status code is most important
    # FastAPI's HTTPException with 401 should return proper HTTP status
