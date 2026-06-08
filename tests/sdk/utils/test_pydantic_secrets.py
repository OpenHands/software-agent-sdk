"""Tests for pydantic_secrets serialization and validation utilities."""

from base64 import urlsafe_b64encode
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import (
    REDACTED_SECRET_VALUE,
    decrypt_str_with_cipher_or_keep,
    is_redacted_secret,
    serialize_secret,
    validate_secret,
    validate_secret_dict,
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


def test_serialize_secret_encrypted_mode_without_cipher_raises_error(
    mock_info,
):
    """expose_secrets='encrypted' without cipher raises ValueError."""
    secret = SecretStr("sk-test-123")
    with pytest.raises(ValueError, match="no cipher configured"):
        serialize_secret(secret, mock_info({"expose_secrets": "encrypted"}))


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


def test_serialize_secret_cipher_with_bool_true_returns_plaintext(mock_info, cipher):
    """expose_secrets=True (legacy boolean) overrides cipher - returns raw value.

    This tests backward compatibility: when expose_secrets=True is passed with
    a cipher, it should return plaintext instead of encrypting.
    """
    secret = SecretStr("sk-test-123")
    result = serialize_secret(
        secret, mock_info({"expose_secrets": True, "cipher": cipher})
    )
    # Should be plaintext, not encrypted
    assert result == "sk-test-123"


# ── validate_secret tests ───────────────────────────────────────────────


def test_validate_secret_none_returns_none(mock_info):
    result = validate_secret(None, mock_info({}))
    assert result is None


def test_validate_secret_invalid_type_int_raises_error(mock_info):
    """validate_secret raises TypeError for invalid int type.

    The function signature expects str | SecretStr | None. Passing an int
    fails when trying to call .strip() on the value.
    """
    with pytest.raises((TypeError, AttributeError)):
        validate_secret(123, mock_info({}))  # type: ignore[arg-type]


def test_validate_secret_invalid_type_dict_returns_none(mock_info):
    """validate_secret handles empty dict gracefully (returns None).

    Empty dict is falsy, so it's treated as empty/missing secret.
    Note: Non-empty dicts would fail when .strip() is called.
    """
    result = validate_secret({}, mock_info({}))  # type: ignore[arg-type]
    assert result is None


def test_validate_secret_invalid_type_list_returns_none(mock_info):
    """validate_secret handles empty list gracefully (returns None).

    Empty list is falsy, so it's treated as empty/missing secret.
    Note: Non-empty lists would fail when .strip() is called.
    """
    result = validate_secret([], mock_info({}))  # type: ignore[arg-type]
    assert result is None


def test_validate_secret_nonempty_dict_raises_error(mock_info):
    """validate_secret raises error for non-empty dict (invalid type)."""
    with pytest.raises((TypeError, AttributeError)):
        validate_secret({"key": "value"}, mock_info({}))  # type: ignore[arg-type]


def test_validate_secret_nonempty_list_raises_error(mock_info):
    """validate_secret raises error for non-empty list (invalid type)."""
    with pytest.raises((TypeError, AttributeError)):
        validate_secret(["value"], mock_info({}))  # type: ignore[arg-type]


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


# ── Real Pydantic integration tests ─────────────────────────────────────


def test_real_pydantic_roundtrip_encrypted(cipher):
    """Test encryption via actual Pydantic serialization (not mocks)."""
    from openhands.agent_server.persistence.models import CustomSecret

    # Create with plaintext
    secret = CustomSecret(name="TEST_KEY", secret=SecretStr("my-secret-value"))

    # Serialize with encrypted context (real model_dump call)
    data = secret.model_dump(
        mode="json", context={"expose_secrets": "encrypted", "cipher": cipher}
    )

    # Verify encrypted (not plaintext, not redacted)
    assert data["secret"] != "my-secret-value"
    assert data["secret"] != REDACTED_SECRET_VALUE
    assert isinstance(data["secret"], str)

    # Validate (decrypt) with cipher context (real model_validate call)
    restored = CustomSecret.model_validate(data, context={"cipher": cipher})
    assert restored.secret is not None
    assert restored.secret.get_secret_value() == "my-secret-value"


def test_real_pydantic_roundtrip_plaintext():
    """Test plaintext via actual Pydantic serialization (not mocks)."""
    from openhands.agent_server.persistence.models import CustomSecret

    # Create with plaintext
    secret = CustomSecret(name="TEST_KEY", secret=SecretStr("my-secret-value"))

    # Serialize with plaintext context
    data = secret.model_dump(mode="json", context={"expose_secrets": "plaintext"})

    # Verify plaintext
    assert data["secret"] == "my-secret-value"

    # Validate (no cipher - just wraps in SecretStr)
    restored = CustomSecret.model_validate(data)
    assert restored.secret is not None
    assert restored.secret.get_secret_value() == "my-secret-value"


def test_real_pydantic_redacted_mode():
    """Test redaction via actual Pydantic serialization (default behavior)."""
    from openhands.agent_server.persistence.models import CustomSecret

    # Create with plaintext
    secret = CustomSecret(name="TEST_KEY", secret=SecretStr("my-secret-value"))

    # Serialize without context (default = redacted)
    data = secret.model_dump(mode="json")

    # Verify redacted - Pydantic returns SecretStr repr for json mode
    # which is "**********" (the default SecretStr repr)
    assert data["secret"] == REDACTED_SECRET_VALUE


def test_real_pydantic_nested_secrets_roundtrip(cipher):
    """Test encryption of nested secrets in Secrets model."""
    from openhands.agent_server.persistence.models import CustomSecret, Secrets

    # Create Secrets with multiple custom secrets
    secrets = Secrets(
        custom_secrets={
            "API_KEY": CustomSecret(
                name="API_KEY", secret=SecretStr("sk-123"), description="API key"
            ),
            "DB_PASS": CustomSecret(
                name="DB_PASS",
                secret=SecretStr("password123"),
                description="DB password",
            ),
        }
    )

    # Serialize with cipher (encrypts all secrets)
    data = secrets.model_dump(mode="json", context={"cipher": cipher})

    # Verify all secrets are encrypted
    for name in ["API_KEY", "DB_PASS"]:
        assert data["custom_secrets"][name]["secret"] not in [
            "sk-123",
            "password123",
            REDACTED_SECRET_VALUE,
        ]

    # Validate (decrypt) all secrets
    restored = Secrets.model_validate(data, context={"cipher": cipher})
    assert restored.custom_secrets["API_KEY"].secret is not None
    assert restored.custom_secrets["API_KEY"].secret.get_secret_value() == "sk-123"
    assert restored.custom_secrets["DB_PASS"].secret is not None
    assert restored.custom_secrets["DB_PASS"].secret.get_secret_value() == "password123"


def test_real_pydantic_persisted_settings_roundtrip(cipher):
    """Test PersistedSettings serialization with encrypted LLM api_key.

    This tests the primary use case: full PersistedSettings with
    agent_settings.llm.api_key encrypted and round-tripped.
    """
    from openhands.agent_server.persistence.models import PersistedSettings

    # Create settings with secret
    settings = PersistedSettings()
    settings.agent_settings.llm.api_key = SecretStr("sk-test-key-12345")

    # Serialize with cipher
    data = settings.model_dump(mode="json", context={"cipher": cipher})
    encrypted_key = data["agent_settings"]["llm"]["api_key"]

    # Should be encrypted (not plaintext, not redacted)
    assert encrypted_key != "sk-test-key-12345"
    assert encrypted_key != REDACTED_SECRET_VALUE

    # Deserialize (decrypt)
    restored = PersistedSettings.model_validate(data, context={"cipher": cipher})
    restored_key = restored.agent_settings.llm.api_key
    assert restored_key is not None
    assert isinstance(restored_key, SecretStr)
    assert restored_key.get_secret_value() == "sk-test-key-12345"


# ── decrypt_str_with_cipher_or_keep / validate_secret_dict tests ────────


def test_decrypt_str_with_cipher_or_keep_decrypts_fernet_token(cipher):
    encrypted = cipher.encrypt(SecretStr("plaintext-value"))
    assert encrypted is not None
    assert (
        decrypt_str_with_cipher_or_keep(cipher, encrypted, description="x")
        == "plaintext-value"
    )


def test_decrypt_str_with_cipher_or_keep_passes_through_non_string(cipher):
    """Non-strings (e.g. nested SecretSource dicts) must pass through unchanged.

    The dict-of-string helper is called against the raw dict at
    mode='before' time, so values that haven't been deserialised yet
    (e.g. SecretSource payloads represented as dicts) must not be
    treated as encrypted blobs.
    """
    assert decrypt_str_with_cipher_or_keep(cipher, 42, description="x") == 42
    assert decrypt_str_with_cipher_or_keep(cipher, None, description="x") is None
    assert decrypt_str_with_cipher_or_keep(
        cipher, {"nested": "dict"}, description="x"
    ) == {"nested": "dict"}


def test_decrypt_str_with_cipher_or_keep_passes_through_legacy_plaintext(cipher):
    """Plaintext from clients that pre-date the encryption pipeline must
    still round-trip cleanly so the next save can re-encrypt it."""
    assert (
        decrypt_str_with_cipher_or_keep(cipher, "sk-plaintext", description="x")
        == "sk-plaintext"
    )


def test_decrypt_str_with_cipher_or_keep_warns_on_undecryptable(cipher, caplog):
    """A garbled ciphertext with the right prefix shouldn't crash —
    log a warning and return the original so an operator can repair."""
    bogus = "gAAAAA" + ("x" * 80)
    with caplog.at_level("WARNING"):
        result = decrypt_str_with_cipher_or_keep(cipher, bogus, description="ACP env")
    assert result == bogus
    assert any("could not be decrypted" in r.message for r in caplog.records)
    assert any("ACP env" in r.message for r in caplog.records)


def test_validate_secret_dict_decrypts_each_value(cipher, mock_info):
    encrypted_a = cipher.encrypt(SecretStr("alpha"))
    encrypted_b = cipher.encrypt(SecretStr("beta"))
    info = mock_info({"cipher": cipher})
    result = validate_secret_dict(
        {"A": encrypted_a, "B": encrypted_b}, info, description="x"
    )
    assert result == {"A": "alpha", "B": "beta"}


def test_validate_secret_dict_without_cipher_is_a_noop(mock_info):
    info = mock_info(None)
    payload = {"A": "ciphertext-shaped-but-no-cipher-to-decrypt"}
    assert validate_secret_dict(payload, info, description="x") == payload


def test_validate_secret_dict_passes_through_non_dict(cipher, mock_info):
    info = mock_info({"cipher": cipher})
    # Lets downstream Pydantic raise the canonical "expected dict" error
    # rather than turning it into a TypeError here.
    assert validate_secret_dict("not-a-dict", info, description="x") == "not-a-dict"
    assert validate_secret_dict(None, info, description="x") is None
