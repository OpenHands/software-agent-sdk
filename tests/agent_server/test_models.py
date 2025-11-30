"""Tests for agent_server models."""

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from openhands.agent_server.models import UpdateSecretsRequest
from openhands.sdk.conversation.secret_source import LookupSecret, StaticSecret


def test_update_secrets_request_string_conversion():
    """Test that plain string secrets are converted to StaticSecret objects."""

    # Test with plain string secrets
    request = UpdateSecretsRequest(
        secrets={  # type: ignore[arg-type]
            "API_KEY": "plain-secret-value",
            "TOKEN": "another-secret",
        }
    )

    # Verify conversion happened
    assert isinstance(request.secrets["API_KEY"], StaticSecret)
    assert isinstance(request.secrets["TOKEN"], StaticSecret)

    # Verify the actual secret values
    assert request.secrets["API_KEY"].get_value() == "plain-secret-value"
    assert request.secrets["TOKEN"].get_value() == "another-secret"


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
    assert isinstance(request.secrets["PLAIN_SECRET"], StaticSecret)
    assert isinstance(request.secrets["STATIC_SECRET"], StaticSecret)
    assert isinstance(request.secrets["LOOKUP_SECRET"], LookupSecret)

    # Verify values
    assert request.secrets["PLAIN_SECRET"].get_value() == "plain-value"
    assert request.secrets["STATIC_SECRET"].get_value() == "static-value"


def test_update_secrets_request_dict_without_kind():
    """Test handling of dict values without 'kind' field."""

    request = UpdateSecretsRequest(
        secrets={  # type: ignore[arg-type]
            "SECRET_WITH_VALUE": {
                "value": "secret-value",
                "description": "A test secret",
            },
        }
    )

    # Secret with value should be converted to StaticSecret
    assert isinstance(request.secrets["SECRET_WITH_VALUE"], StaticSecret)
    assert request.secrets["SECRET_WITH_VALUE"].get_value() == "secret-value"


def test_update_secrets_request_invalid_dict():
    """Test handling of invalid dict values without 'kind' or 'value' field."""

    # This should raise an error since the dict is invalid
    # The error could be KeyError or ValidationError depending on where it fails
    with pytest.raises((ValidationError, KeyError)) as exc_info:
        UpdateSecretsRequest(
            secrets={  # type: ignore[arg-type]
                "SECRET_WITHOUT_VALUE": {"description": "No value"},
            }
        )

    # Verify the error is about the missing 'kind' field
    error_details = str(exc_info.value)
    assert "kind" in error_details.lower()


def test_update_secrets_request_empty_secrets():
    """Test that empty secrets dict is handled correctly."""

    request = UpdateSecretsRequest(secrets={})
    assert request.secrets == {}


def test_update_secrets_request_invalid_input():
    """Test that invalid input types are handled appropriately."""

    # Non-dict input should be preserved (will fail validation later)
    with pytest.raises(ValidationError):
        UpdateSecretsRequest(secrets="not-a-dict")  # type: ignore[arg-type]
