"""Tests for pydantic_secrets serialization and validation utilities."""

from base64 import urlsafe_b64encode
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import (
    REDACTED_SECRET_VALUE,
    is_redacted_secret,
    serialize_secret,
    validate_secret,
)


@pytest.fixture
def cipher():
    """Create a cipher for testing."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    return Cipher(key)


@pytest.fixture
def mock_info():
    """Create a mock SerializationInfo/ValidationInfo."""

    def create_info(context=None):
        info = MagicMock()
        info.context = context
        return info

    return create_info


# ── is_redacted_secret tests ────────────────────────────────────────────


def test_is_redacted_secret_with_redacted_string():
    assert is_redacted_secret(REDACTED_SECRET_VALUE) is True


def test_is_redacted_secret_with_redacted_secretstr():
    assert is_redacted_secret(SecretStr(REDACTED_SECRET_VALUE)) is True


def test_is_redacted_secret_with_normal_string():
    assert is_redacted_secret("sk-test-123") is False


def test_is_redacted_secret_with_normal_secretstr():
    assert is_redacted_secret(SecretStr("sk-test-123")) is False


def test_is_redacted_secret_with_none():
    assert is_redacted_secret(None) is False


# ── serialize_secret tests ──────────────────────────────────────────────


def test_serialize_secret_none_returns_none(mock_info):
    result = serialize_secret(None, mock_info({}))
    assert result is None


def test_serialize_secret_no_context_returns_secretstr(mock_info):
    """Without context, return SecretStr for Pydantic default masking."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(secret, mock_info(None))
    assert isinstance(result, SecretStr)
    assert result.get_secret_value() == "sk-test-123"


def test_serialize_secret_empty_context_returns_secretstr(mock_info):
    """Empty context = no exposure, return SecretStr."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(secret, mock_info({}))
    assert isinstance(result, SecretStr)


def test_serialize_secret_plaintext_mode(mock_info):
    """expose_secrets='plaintext' returns raw value."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(secret, mock_info({"expose_secrets": "plaintext"}))
    assert result == "sk-test-123"


def test_serialize_secret_plaintext_mode_bool_true(mock_info):
    """expose_secrets=True (legacy) returns raw value."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(secret, mock_info({"expose_secrets": True}))
    assert result == "sk-test-123"


def test_serialize_secret_encrypted_mode_with_cipher(mock_info, cipher):
    """expose_secrets='encrypted' with cipher encrypts the value."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(
        secret, mock_info({"expose_secrets": "encrypted", "cipher": cipher})
    )
    # Should be encrypted (not plaintext, not redacted)
    assert result != "sk-test-123"
    assert result != REDACTED_SECRET_VALUE
    assert isinstance(result, str)
    # Should be decryptable
    decrypted = cipher.decrypt(result)
    assert decrypted.get_secret_value() == "sk-test-123"


def test_serialize_secret_encrypted_mode_without_cipher_falls_back_to_redacted(
    mock_info,
):
    """expose_secrets='encrypted' without cipher falls back to redaction."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(secret, mock_info({"expose_secrets": "encrypted"}))
    assert result == REDACTED_SECRET_VALUE


def test_serialize_secret_cipher_without_expose_mode_encrypts(mock_info, cipher):
    """Cipher in context without expose_secrets still encrypts (backward compat)."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(secret, mock_info({"cipher": cipher}))
    assert result != "sk-test-123"
    # Should be decryptable
    decrypted = cipher.decrypt(result)
    assert decrypted.get_secret_value() == "sk-test-123"


def test_serialize_secret_cipher_with_plaintext_mode_returns_plaintext(
    mock_info, cipher
):
    """expose_secrets='plaintext' overrides cipher - returns raw value."""
    secret = SecretStr("sk-test-123")
    result = serialize_secret(
        secret, mock_info({"expose_secrets": "plaintext", "cipher": cipher})
    )
    assert result == "sk-test-123"


# ── validate_secret tests ───────────────────────────────────────────────


def test_validate_secret_none_returns_none(mock_info):
    result = validate_secret(None, mock_info({}))
    assert result is None


def test_validate_secret_string_returns_secretstr(mock_info):
    result = validate_secret("sk-test-123", mock_info({}))
    assert isinstance(result, SecretStr)
    assert result.get_secret_value() == "sk-test-123"


def test_validate_secret_secretstr_passthrough(mock_info):
    secret = SecretStr("sk-test-123")
    result = validate_secret(secret, mock_info({}))
    assert isinstance(result, SecretStr)
    assert result.get_secret_value() == "sk-test-123"


def test_validate_secret_empty_string_returns_none(mock_info):
    result = validate_secret("", mock_info({}))
    assert result is None


def test_validate_secret_whitespace_only_returns_none(mock_info):
    result = validate_secret("   ", mock_info({}))
    assert result is None


def test_validate_secret_redacted_value_returns_none(mock_info):
    result = validate_secret(REDACTED_SECRET_VALUE, mock_info({}))
    assert result is None


def test_validate_secret_with_cipher_decrypts(mock_info, cipher):
    """Cipher in context triggers decryption."""
    secret = SecretStr("sk-test-123")
    encrypted = cipher.encrypt(secret)

    result = validate_secret(encrypted, mock_info({"cipher": cipher}))
    assert isinstance(result, SecretStr)
    assert result.get_secret_value() == "sk-test-123"


def test_validate_secret_with_cipher_invalid_data_returns_none(mock_info, cipher):
    """Invalid encrypted data with cipher returns None (graceful failure)."""
    result = validate_secret("not-encrypted-data", mock_info({"cipher": cipher}))
    assert result is None


def test_validate_secret_with_cipher_wrong_key_returns_none(mock_info, cipher):
    """Wrong cipher key returns None (graceful failure)."""
    # Encrypt with one key
    secret = SecretStr("sk-test-123")
    encrypted = cipher.encrypt(secret)

    # Try to decrypt with different key
    other_key = urlsafe_b64encode(b"b" * 32).decode("ascii")
    other_cipher = Cipher(other_key)

    result = validate_secret(encrypted, mock_info({"cipher": other_cipher}))
    assert result is None


# ── Round-trip tests ────────────────────────────────────────────────────


def test_roundtrip_encrypted_mode(mock_info, cipher):
    """Full round-trip: serialize with encrypted mode, validate with cipher."""
    original = SecretStr("sk-test-api-key-12345")

    # Serialize with encrypted mode
    encrypted = serialize_secret(
        original, mock_info({"expose_secrets": "encrypted", "cipher": cipher})
    )
    assert encrypted != "sk-test-api-key-12345"

    # Validate (decrypt) with cipher
    decrypted = validate_secret(encrypted, mock_info({"cipher": cipher}))
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-test-api-key-12345"


def test_roundtrip_plaintext_mode(mock_info):
    """Round-trip with plaintext mode (no encryption)."""
    original = SecretStr("sk-test-api-key-12345")

    # Serialize with plaintext mode
    plaintext = serialize_secret(original, mock_info({"expose_secrets": "plaintext"}))
    assert plaintext == "sk-test-api-key-12345"

    # Validate (just wraps in SecretStr)
    result = validate_secret(plaintext, mock_info({}))
    assert result is not None
    assert result.get_secret_value() == "sk-test-api-key-12345"
