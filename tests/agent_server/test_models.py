"""Tests for agent_server models."""

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from openhands.agent_server.models import UpdateSecretsRequest
from openhands.sdk.conversation.secret_source import LookupSecret, StaticSecret


def test_update_secrets_request_accepts_strings():
    """Test that plain string secrets are accepted as strings."""

    request = UpdateSecretsRequest(
        secrets={  # type: ignore[arg-type]
            "API_KEY": "plain-secret-value",
            "TOKEN": "another-secret",
        }
    )

    assert request.secrets["API_KEY"] == "plain-secret-value"
    assert request.secrets["TOKEN"] == "another-secret"


def test_update_secrets_request_proper_secret_source():
    """Test that proper SecretSource objects are not modified."""

    static_secret = StaticSecret(value=SecretStr("static-value"))
    lookup_secret = LookupSecret(url="https://example.com/secret")

    request = UpdateSecretsRequest(
        secrets={
            "STATIC_SECRET": static_secret,
            "LOOKUP_SECRET": lookup_secret,
        }
    )

    # Verify objects are preserved
    assert request.secrets["STATIC_SECRET"] is static_secret
    assert request.secrets["LOOKUP_SECRET"] is lookup_secret
    assert isinstance(request.secrets["STATIC_SECRET"], StaticSecret)
    assert isinstance(request.secrets["LOOKUP_SECRET"], LookupSecret)


def test_update_secrets_request_mixed_formats():
    """Test that mixed formats (strings and SecretSource objects) work together."""

    secrets_dict: dict[str, Any] = {
        "PLAIN_SECRET": "plain-value",
        "STATIC_SECRET": StaticSecret(value=SecretStr("static-value")),
        "LOOKUP_SECRET": LookupSecret(url="https://example.com/secret"),
    }
    request = UpdateSecretsRequest(secrets=secrets_dict)  # type: ignore[arg-type]

    # Verify all types are correct
    assert request.secrets["PLAIN_SECRET"] == "plain-value"
    assert isinstance(request.secrets["STATIC_SECRET"], StaticSecret)
    assert isinstance(request.secrets["LOOKUP_SECRET"], LookupSecret)

    # Verify values
    assert request.secrets["STATIC_SECRET"].get_value() == "static-value"


def test_update_secrets_request_rejects_ambiguous_dicts():
    """Dicts without 'kind' should not be auto-coerced at the API boundary."""

    with pytest.raises(ValidationError):
        bad: Any = {
            "SECRET_WITH_VALUE": {
                "value": "secret-value",
                "description": "A test secret",
            }
        }
        UpdateSecretsRequest(secrets=bad)


def test_update_secrets_request_invalid_dict():
    """Dicts must include a discriminant 'kind' when not strings."""

    with pytest.raises(ValidationError):
        bad: Any = {"SECRET_WITHOUT_VALUE": {"description": "No value"}}
        UpdateSecretsRequest(secrets=bad)


def test_update_secrets_request_empty_secrets():
    """Test that empty secrets dict is handled correctly."""

    request = UpdateSecretsRequest(secrets={})
    assert request.secrets == {}


def test_update_secrets_request_invalid_input():
    """Test that invalid input types are handled appropriately."""

    with pytest.raises(ValidationError):
        UpdateSecretsRequest(secrets="not-a-dict")  # type: ignore[arg-type]
