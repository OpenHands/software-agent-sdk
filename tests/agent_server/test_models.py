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

    # Verify values are correct
    assert request.secrets["API_KEY"].get_value() == "plain-secret-value"
    assert request.secrets["TOKEN"].get_value() == "another-secret"


def test_update_secrets_request_proper_secret_source():
    """Test that properly formatted SecretSource objects are preserved."""

    # Test with properly formatted SecretSource objects
    request = UpdateSecretsRequest(
        secrets={
            "STATIC_SECRET": StaticSecret(value=SecretStr("static-value")),
            "LOOKUP_SECRET": LookupSecret(url="https://example.com/secret"),
        }
    )

    # Verify objects are preserved as-is
    assert isinstance(request.secrets["STATIC_SECRET"], StaticSecret)
    assert isinstance(request.secrets["LOOKUP_SECRET"], LookupSecret)

    # Verify values
    assert request.secrets["STATIC_SECRET"].get_value() == "static-value"
    assert request.secrets["LOOKUP_SECRET"].url == "https://example.com/secret"


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
    assert request.secrets["LOOKUP_SECRET"].url == "https://example.com/secret"


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
    assert request.secrets["SECRET_WITH_VALUE"].description == "A test secret"


def test_update_secrets_request_invalid_dict():
    """Test handling of invalid dict values without 'kind' or 'value' field."""

    # This should raise a validation error since the dict is invalid
    with pytest.raises(ValidationError):
        UpdateSecretsRequest(
            secrets={  # type: ignore[arg-type]
                "SECRET_WITHOUT_VALUE": {"description": "No value"},
            }
        )


def test_update_secrets_request_empty_secrets():
    """Test that empty secrets dict is handled correctly."""

    request = UpdateSecretsRequest(secrets={})
    assert request.secrets == {}


def test_update_secrets_request_invalid_input():
    """Test that invalid input types are handled appropriately."""

    # Non-dict input should be preserved (will fail validation later)
    with pytest.raises(ValidationError):
        UpdateSecretsRequest(secrets="not-a-dict")  # type: ignore[arg-type]
