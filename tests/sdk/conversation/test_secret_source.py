"""Tests for SecretSources class."""

from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk.secret import LookupSecret, StaticSecret
from openhands.sdk.utils.cipher import Cipher


@pytest.fixture
def lookup_secret():
    return LookupSecret(
        url="https://my-oauth-service.com",
        headers={
            "authorization": "Bearer Token",
            "cookie": "sessionid=abc123;",
            "x-access-token": "token-abc123",
            "some-key": "a key",
            "not-sensitive": "hello there",
        },
    )


def test_lookup_secret_serialization_default(lookup_secret):
    """Test LookupSecret serialization"""
    dumped = lookup_secret.model_dump(mode="json")
    expected = {
        "kind": "LookupSecret",
        "description": None,
        "url": "https://my-oauth-service.com",
        "headers": {
            "authorization": "**********",
            "cookie": "**********",
            "x-access-token": "**********",
            "some-key": "**********",
            "not-sensitive": "hello there",
        },
    }
    assert dumped == expected


def test_lookup_secret_serialization_expose_secrets(lookup_secret):
    """Test LookupSecret serialization"""
    dumped = lookup_secret.model_dump(mode="json", context={"expose_secrets": True})
    expected = {
        "kind": "LookupSecret",
        "description": None,
        "url": "https://my-oauth-service.com",
        "headers": {
            "authorization": "Bearer Token",
            "cookie": "sessionid=abc123;",
            "x-access-token": "token-abc123",
            "some-key": "a key",
            "not-sensitive": "hello there",
        },
    }
    assert dumped == expected
    validated = LookupSecret.model_validate(dumped)
    assert validated == lookup_secret


def test_lookup_secret_serialization_encrypt(lookup_secret):
    """Test LookupSecret serialization"""
    cipher = Cipher(secret_key="some secret key")
    dumped = lookup_secret.model_dump(mode="json", context={"cipher": cipher})
    validated = LookupSecret.model_validate(dumped, context={"cipher": cipher})
    assert validated == lookup_secret


def test_lookup_secret_deserialization_redacted_headers():
    """Test LookupSecret can be deserialized with redacted header values.

    This is a regression test for issue 1505 where LookupSecret headers with
    redacted (masked) values would fail to deserialize due to assertion errors.
    """
    # Simulate the serialized state with redacted headers
    serialized = {
        "kind": "LookupSecret",
        "description": None,
        "url": "https://my-oauth-service.com",
        "headers": {
            "authorization": "**********",  # Redacted
            "cookie": "**********",  # Redacted
            "x-access-token": "**********",  # Redacted
            "some-key": "**********",  # Redacted
            "not-sensitive": "hello there",  # Not a secret header
        },
    }

    # This was failing before the fix with assertion error
    validated = LookupSecret.model_validate(serialized)

    # The secret headers should be stripped out since they're redacted
    assert validated.url == "https://my-oauth-service.com"
    # Secret headers should be removed (since their values were redacted)
    assert "authorization" not in validated.headers
    assert "cookie" not in validated.headers
    assert "x-access-token" not in validated.headers
    assert "some-key" not in validated.headers
    # Non-sensitive headers should be preserved
    assert validated.headers["not-sensitive"] == "hello there"


def test_static_secret_optional_value():
    """Test StaticSecret works with optional value (None default).

    This is a regression test for issue 1505 where StaticSecret.value was
    a required field causing deserialization to fail when secrets were
    redacted (converted to None).
    """
    # Test with value
    secret_with_value = StaticSecret(value=SecretStr("test-secret"))
    assert secret_with_value.get_value() == "test-secret"

    # Test with None value (default)
    secret_without_value = StaticSecret()
    assert secret_without_value.value is None
    assert secret_without_value.get_value() is None

    # Test deserialization with None value
    serialized = {"kind": "StaticSecret", "value": None}
    validated = StaticSecret.model_validate(serialized)
    assert validated.value is None
    assert validated.get_value() is None


def test_static_secret_deserialization_redacted():
    """Test StaticSecret can be deserialized from redacted value.

    This is a regression test for issue 1505.
    """
    # Simulate the serialized state with redacted value
    serialized = {"kind": "StaticSecret", "value": "**********"}

    # This was failing before the fix
    validated = StaticSecret.model_validate(serialized)

    # The value should be None since it was redacted
    assert validated.value is None
    assert validated.get_value() is None


def test_lookup_secret_redacts_token_and_cookie_headers():
    """Test that X-Access-Token and Cookie headers are properly redacted.

    This is a regression test to prevent leaking authentication tokens in
    trajectory exports. Headers like X-Access-Token and Cookie should be
    treated as sensitive and redacted during serialization.
    """
    secret = LookupSecret(
        url="https://api.example.com/secrets",
        headers={
            "X-Access-Token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "Cookie": "session_id=abc123; keycloak_auth=eyJhbGci...",
            "X-Auth-Token": "bearer_token_value",
            "Content-Type": "application/json",
        },
    )

    # Serialize without expose_secrets context (default behavior)
    serialized = secret.model_dump(mode="json")

    # Check that token-based headers are redacted
    assert serialized["headers"]["X-Access-Token"] == "**********"
    assert serialized["headers"]["Cookie"] == "**********"
    assert serialized["headers"]["X-Auth-Token"] == "**********"

    # Check that non-secret headers are preserved
    assert serialized["headers"]["Content-Type"] == "application/json"


def test_lookup_secret_validate_with_cipher_preserves_plaintext_headers():
    """Plaintext auth headers must survive validation when a cipher is in
    the context.

    Regression test: agent-canvas (and any other client that round-trips
    encrypted agent secrets via ``secrets_encrypted=True``) sends a
    ``LookupSecret`` whose ``headers`` carry a plaintext ``X-Session-API-Key``
    used to authenticate the lazy lookup. The validator used to feed that
    plaintext header through ``cipher.decrypt`` (because the header name
    matches a secret pattern), which fails and used to drop the header
    silently. The runtime ``httpx.get`` then made an unauthenticated request
    to the agent-server and got a 401, so the secret value was never
    available to the conversation.
    """
    cipher = Cipher(secret_key="some secret key")
    plaintext_session_key = "plaintext-session-api-key-value"

    serialized = {
        "kind": "LookupSecret",
        "url": "http://localhost:8000/api/settings/secrets/MY_TOKEN",
        "headers": {
            "X-Session-API-Key": plaintext_session_key,
            "Content-Type": "application/json",
        },
    }

    validated = LookupSecret.model_validate(serialized, context={"cipher": cipher})

    # Plaintext auth header survives despite cipher being in context.
    assert validated.headers["X-Session-API-Key"] == plaintext_session_key
    # Non-secret headers are still pass-through.
    assert validated.headers["Content-Type"] == "application/json"


def test_lookup_secret_validate_with_cipher_decrypts_encrypted_headers():
    """Round-trip encrypted headers with cipher should still decrypt.

    Companion to the plaintext test above: when a header was actually
    encrypted with the same cipher (e.g. loaded from at-rest storage),
    validation must still decrypt it back to plaintext rather than treating
    it as opaque ciphertext.
    """
    cipher = Cipher(secret_key="some secret key")
    secret = LookupSecret(
        url="https://my-oauth-service.com",
        headers={"Authorization": "Bearer real-token"},
    )

    dumped = secret.model_dump(mode="json", context={"cipher": cipher})
    # Sanity check: the header is encrypted on the wire.
    assert dumped["headers"]["Authorization"] != "Bearer real-token"

    validated = LookupSecret.model_validate(dumped, context={"cipher": cipher})
    assert validated.headers["Authorization"] == "Bearer real-token"


def test_lookup_secret_validate_with_cipher_drops_redacted_headers():
    """Redacted headers must still be dropped, even when a cipher is set.

    Confirms the plaintext-fallback fix doesn't accidentally resurrect
    masked values like ``"**********"`` as if they were real auth material.
    """
    cipher = Cipher(secret_key="some secret key")
    serialized = {
        "kind": "LookupSecret",
        "url": "https://my-oauth-service.com",
        "headers": {
            "Authorization": "**********",
            "X-Access-Token": "",
            "Content-Type": "application/json",
        },
    }

    validated = LookupSecret.model_validate(serialized, context={"cipher": cipher})
    assert "Authorization" not in validated.headers
    assert "X-Access-Token" not in validated.headers
    assert validated.headers["Content-Type"] == "application/json"


def test_lookup_secret_author_header_not_redacted():
    """Test that legitimate 'Author' headers are NOT falsely redacted.

    Regression test to ensure substring pattern matching doesn't cause
    false positives with headers like Author, Co-Author, GitHub-Author.
    """
    secret = LookupSecret(
        url="https://api.example.com/data",
        headers={
            "Author": "john.doe@example.com",
            "Co-Author": "jane.doe@example.com",
            "GitHub-Author": "contributor@example.com",
            "Authorization": "Bearer secret_token",
        },
    )

    serialized = secret.model_dump(mode="json")

    # Author-related headers should NOT be redacted (false positive check)
    assert serialized["headers"]["Author"] == "john.doe@example.com"
    assert serialized["headers"]["Co-Author"] == "jane.doe@example.com"
    assert serialized["headers"]["GitHub-Author"] == "contributor@example.com"

    # But Authorization should be redacted
    assert serialized["headers"]["Authorization"] == "**********"


def test_lookup_secret_relative_url_uses_current_server(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:4321")

    secret = LookupSecret(url="/api/settings/secrets/OPENAI_API_KEY")

    assert secret.url == "http://127.0.0.1:4321/api/settings/secrets/OPENAI_API_KEY"


def test_lookup_secret_get_value_resolves_relative_url(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:4321")
    response = Mock(text="resolved-secret")
    response.raise_for_status = Mock()

    with patch(
        "openhands.sdk.secret.secrets.httpx.get", return_value=response
    ) as mock_get:
        secret = LookupSecret(url="api/settings/secrets/OPENAI_API_KEY")

        assert secret.get_value() == "resolved-secret"

    mock_get.assert_called_once_with(
        "http://127.0.0.1:4321/api/settings/secrets/OPENAI_API_KEY",
        headers={},
        timeout=30.0,
    )
