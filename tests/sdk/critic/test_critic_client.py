"""Tests for CriticClient api_key handling."""

import pytest
from pydantic import SecretStr, ValidationError

from openhands.sdk.critic.impl.api.client import CriticClient


def test_critic_client_with_str_api_key():
    """Test CriticClient accepts str api_key and converts to SecretStr."""
    client = CriticClient(api_key="test_api_key_123")

    assert isinstance(client.api_key, SecretStr)
    assert client.api_key.get_secret_value() == "test_api_key_123"


def test_critic_client_with_secret_str_api_key():
    """Test that CriticClient accepts a SecretStr api_key directly."""
    secret_key = SecretStr("secret_api_key_456")
    client = CriticClient(api_key=secret_key)

    assert isinstance(client.api_key, SecretStr)
    assert client.api_key.get_secret_value() == "secret_api_key_456"


def test_critic_client_empty_string_api_key():
    """Test that CriticClient rejects an empty string api_key."""
    with pytest.raises(ValidationError, match="api_key must be non-empty"):
        CriticClient(api_key="")


def test_critic_client_whitespace_only_api_key():
    """Test that CriticClient rejects a whitespace-only api_key."""
    with pytest.raises(ValidationError, match="api_key must be non-empty"):
        CriticClient(api_key="   \t\n  ")


def test_critic_client_empty_secret_str_api_key():
    """Test that CriticClient rejects an empty SecretStr api_key."""
    with pytest.raises(ValidationError, match="api_key must be non-empty"):
        CriticClient(api_key=SecretStr(""))


def test_critic_client_whitespace_secret_str_api_key():
    """Test that CriticClient rejects a whitespace-only SecretStr api_key."""
    with pytest.raises(ValidationError, match="api_key must be non-empty"):
        CriticClient(api_key=SecretStr("   \t\n  "))


def test_critic_client_api_key_not_exposed_in_repr():
    """Test that the api_key is not exposed in the string representation."""
    client = CriticClient(api_key="super_secret_key")

    client_repr = repr(client)
    client_str = str(client)

    # SecretStr should hide the actual key value in repr/str
    assert "super_secret_key" not in client_repr
    assert "super_secret_key" not in client_str


def test_critic_client_api_key_preserved_after_validation():
    """Test that the api_key value is correctly preserved after validation."""
    test_key = "my_test_key_789"
    client = CriticClient(api_key=test_key)

    # Verify the key is preserved correctly
    assert isinstance(client.api_key, SecretStr)
    assert client.api_key.get_secret_value() == test_key

    # Verify it works with SecretStr input too
    secret_key = SecretStr("another_key_101112")
    client2 = CriticClient(api_key=secret_key)
    assert isinstance(client2.api_key, SecretStr)
    assert client2.api_key.get_secret_value() == "another_key_101112"
