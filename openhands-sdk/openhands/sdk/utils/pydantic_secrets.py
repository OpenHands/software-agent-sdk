import logging
from typing import Literal

from pydantic import SecretStr

from openhands.sdk.utils.cipher import Cipher


REDACTED_SECRET_VALUE = "**********"

# Type for expose_secrets context value
ExposeSecretsMode = Literal["encrypted", "plaintext"] | bool

_logger = logging.getLogger(__name__)


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

    expose_mode = info.context.get("expose_secrets") if info.context else None
    cipher: Cipher | None = info.context.get("cipher") if info.context else None

    # Handle plaintext mode first - no encryption needed
    if expose_mode == "plaintext" or expose_mode is True:
        return v.get_secret_value()

    # Handle encrypted mode (explicit or implicit via cipher presence)
    # When cipher is present without explicit expose_mode, default to encryption
    # This provides backward compatibility for storage operations
    if expose_mode == "encrypted" or cipher:
        if not cipher:
            # Encrypted mode explicitly requested but no cipher available
            raise ValueError(
                "Cannot encrypt secret: no cipher configured. "
                "Set OH_SECRET_KEY environment variable."
            )
        return cipher.encrypt(v)

    # Default: let Pydantic handle masking (redaction)
    return v


def validate_secret(v: str | SecretStr | None, info) -> SecretStr | None:
    """
    Deserialize secret fields, handling encryption and empty values.

    Accepts both str and SecretStr inputs, always returns SecretStr | None.
    - Empty secrets are converted to None
    - Plain strings are converted to SecretStr
    - If a cipher is provided in context, attempts to decrypt the value
    - If decryption fails, the cipher returns None and a warning is logged
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

    # check if a cipher is supplied
    if info.context and info.context.get("cipher"):
        cipher: Cipher = info.context.get("cipher")
        return cipher.decrypt(secret_value)

    # Always return SecretStr
    if isinstance(v, SecretStr):
        return v
    else:
        return SecretStr(secret_value)
