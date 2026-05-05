"""Tests for serialize_secrets_dict utility."""

from __future__ import annotations

from unittest.mock import MagicMock

from pydantic import SecretStr

from openhands.sdk.utils.pydantic_secrets import (
    REDACTED_SECRET_VALUE,
    serialize_secrets_dict,
)


def _fake_info(context: dict | None = None):
    info = MagicMock()
    info.context = context
    return info


def test_default_redacts_all_values():
    d = {"OPENAI_API_KEY": "sk-secret", "BASE_URL": "https://example.com"}
    result = serialize_secrets_dict(d, _fake_info())
    assert result == {
        "OPENAI_API_KEY": REDACTED_SECRET_VALUE,
        "BASE_URL": REDACTED_SECRET_VALUE,
    }


def test_expose_secrets_returns_real_values():
    d = {"KEY": "real-value"}
    result = serialize_secrets_dict(d, _fake_info({"expose_secrets": True}))
    assert result == {"KEY": "real-value"}


def test_expose_secrets_returns_copy():
    d = {"KEY": "value"}
    result = serialize_secrets_dict(d, _fake_info({"expose_secrets": True}))
    assert result is not d


def test_cipher_encrypts_values():
    cipher = MagicMock()
    cipher.encrypt.side_effect = lambda v: f"enc:{v.get_secret_value()}"
    d = {"A": "secret-a", "B": "secret-b"}
    result = serialize_secrets_dict(d, _fake_info({"cipher": cipher}))
    assert result == {"A": "enc:secret-a", "B": "enc:secret-b"}
    assert cipher.encrypt.call_count == 2
    # Verify SecretStr was passed
    for call in cipher.encrypt.call_args_list:
        assert isinstance(call.args[0], SecretStr)


def test_empty_dict():
    result = serialize_secrets_dict({}, _fake_info())
    assert result == {}
