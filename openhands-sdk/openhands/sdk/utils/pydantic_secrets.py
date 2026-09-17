import logging
from collections.abc import Mapping
from typing import Any, Literal, overload

from pydantic import SecretStr, ValidationInfo

from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX, Cipher


REDACTED_SECRET_VALUE = "**********"

# Type for expose_secrets context value
ExposeSecretsMode = Literal["encrypted", "plaintext"] | bool

ResolvedExposeMode = Literal["plaintext", "encrypted", "redact"]

_logger = logging.getLogger(__name__)


class MissingCipherError(ValueError):
    """Raised by ``serialize_secret`` when encryption is requested without a cipher."""


def resolve_expose_mode(context: Mapping[str, Any] | None) -> ResolvedExposeMode:
    """Resolve a Pydantic context to plaintext / encrypted / redact.

    Cipher presence implies ``"encrypted"`` (storage-path opt-in) unless
    ``expose_secrets`` overrides.
    """
    if not context:
        return "redact"
    expose_mode = context.get("expose_secrets")
    if expose_mode == "plaintext" or expose_mode is True:
        return "plaintext"
    if expose_mode == "encrypted" or context.get("cipher") is not None:
        return "encrypted"
    return "redact"


def is_redacted_secret(v: str | SecretStr | None) -> bool:
    if v is None:
        return False
    if isinstance(v, SecretStr):
        return v.get_secret_value() == REDACTED_SECRET_VALUE
    return v == REDACTED_SECRET_VALUE


def serialize_secret(v: SecretStr | None, info):
    """
    Serialize secret fields with encryption, plaintext exposure, or redaction.

    Context options:
    - ``cipher``: If provided, encrypts the secret value (takes precedence)
    - ``expose_secrets``: Controls how secrets are exposed:
      - ``"encrypted"``: Encrypt using cipher from context (requires cipher)
      - ``"plaintext"`` or ``True``: Expose the actual value (backend use only)
      - ``False`` or absent: Let Pydantic handle default masking (redaction)

    The ``"encrypted"`` mode is safe for frontend clients as they cannot decrypt.
    The ``"plaintext"`` mode should only be used by trusted backend clients.
    """
    if v is None:
        return None

    mode = resolve_expose_mode(info.context)

    if mode == "plaintext":
        return v.get_secret_value()

    if mode == "encrypted":
        cipher: Cipher | None = info.context.get("cipher") if info.context else None
        if cipher is None:
            raise MissingCipherError(
                "Cannot encrypt secret: no cipher configured. "
                "Set OH_SECRET_KEY environment variable."
            )
        raw = v.get_secret_value()
        if raw.startswith(FERNET_TOKEN_PREFIX) and cipher.try_decrypt_str(raw):
            return raw
        return cipher.encrypt(v)

    return v


def validate_secret(v: str | SecretStr | None, info) -> SecretStr | None:
    """
    Deserialize secret fields, handling encryption and empty values.

    Accepts both str and SecretStr inputs, always returns SecretStr | None.
    - Empty secrets are converted to None
    - Plain strings are converted to SecretStr
    - If a cipher is provided in context and the value is a Fernet token,
      attempts to decrypt the value
    - If Fernet decryption fails, the cipher returns None and a warning is logged
    - Non-token strings pass through as plaintext for legacy settings and PATCHes
    - This gracefully handles conversations encrypted with different keys or were redacted
    """  # noqa: E501
    if v is None:
        return None

    # Handle both SecretStr and string inputs
    if isinstance(v, SecretStr):
        secret_value = v.get_secret_value()
    else:
        secret_value = v

    # If the secret is empty, whitespace-only or redacted - return None
    if not secret_value or not secret_value.strip() or is_redacted_secret(secret_value):
        return None

    # Check if a cipher is supplied and the value is an encrypted Fernet token.
    # Legacy plaintext settings and ordinary PATCH payloads should not be
    # dropped just because the storage layer has encryption configured.
    if (
        info.context
        and info.context.get("cipher")
        and secret_value.startswith(FERNET_TOKEN_PREFIX)
    ):
        cipher: Cipher = info.context.get("cipher")
        return cipher.decrypt(secret_value)

    # Always return SecretStr
    if isinstance(v, SecretStr):
        return v
    else:
        return SecretStr(secret_value)


@overload
def decrypt_str_with_cipher_or_keep(
    cipher: Cipher, value: str, *, description: str = ...
) -> str: ...


@overload
def decrypt_str_with_cipher_or_keep(
    cipher: Cipher, value: Any, *, description: str = ...
) -> Any: ...


def decrypt_str_with_cipher_or_keep(
    cipher: Cipher,
    value: Any,
    *,
    description: str = "secret",
) -> Any:
    """Decrypt a single Fernet-encrypted string in place.

    Returned unchanged when the value isn't a string, doesn't look like a
    Fernet token (legacy plaintext from clients that pre-date the
    encryption pipeline), or fails to decrypt (cipher mismatch / token
    corruption — logged so the operator can repair rather than the
    object dying at construction).

    Building block for the dict-of-string secret-bearing fields
    (`agent_context.secrets`, MCP server `env`/`headers`)
    where each value is a per-key plaintext that's separately
    encrypted at rest — they can't be typed as :class:`SecretStr`
    because their keys are user-supplied.
    """
    if not isinstance(value, str):
        return value
    if not value.startswith(FERNET_TOKEN_PREFIX):
        return value
    decrypted = cipher.try_decrypt_str(value)
    if decrypted is None:
        _logger.warning(
            "%s value looks encrypted but could not be decrypted "
            "(cipher mismatch or corruption); leaving the ciphertext in place.",
            description,
        )
        return value
    return decrypted


def validate_secret_dict(
    value: Any,
    info: ValidationInfo,
    *,
    description: str = "secret",
) -> Any:
    """Decrypt every Fernet-encrypted entry in a ``dict[str, str]`` field.

    Mirrors :func:`validate_secret` for fields whose secret values live
    in a per-key dict (env-var maps, header maps, agent-context
    secrets). Use as a ``field_validator(mode="before")`` hook:

    .. code-block:: python

        @field_validator("env", mode="before")
        @classmethod
        def _decrypt_env(cls, value, info):
            return validate_secret_dict(value, info, description="MCP env")

    No-ops when the field isn't a dict (lets downstream validation
    raise the canonical type error), and when no cipher is in context
    (legacy plaintext callers, or contexts that have already done the
    decryption).
    """
    if not isinstance(value, dict):
        return value
    cipher: Cipher | None = info.context.get("cipher") if info.context else None
    if cipher is None:
        return value
    return {
        k: decrypt_str_with_cipher_or_keep(cipher, v, description=description)
        for k, v in value.items()
    }
