from pydantic import SecretStr

from openhands.sdk.utils.cipher import Cipher


REDACTED_SECRET_VALUE = "**********"


def is_redacted_secret(v: str | SecretStr | None) -> bool:
    if v is None:
        return False
    if isinstance(v, SecretStr):
        return v.get_secret_value() == REDACTED_SECRET_VALUE
    return v == REDACTED_SECRET_VALUE


def serialize_secret(v: SecretStr | None, info):
    """
    Serialize secret fields with encryption or redaction.

    - If a cipher is provided in context, encrypts the secret value
    - If expose_secrets flag is True in context, exposes the actual value
    - Otherwise, lets Pydantic handle default masking (redaction)
    - This prevents accidental secret disclosure
    """  # noqa: E501
    if v is None:
        return None

    # check if a cipher is supplied
    if info.context and info.context.get("cipher"):
        cipher: Cipher = info.context.get("cipher")
        return cipher.encrypt(v)

    # Check if the 'expose_secrets' flag is in the serialization context
    if info.context and info.context.get("expose_secrets"):
        return v.get_secret_value()

    # Let Pydantic handle the default masking
    return v


def serialize_secrets_dict(d: dict[str, str], info) -> dict[str, str]:
    """Serialize a ``dict[str, str]`` whose **values** are all secrets.

    Follows the same contract as :func:`serialize_secret`:

    * *cipher* in context → each value is encrypted.
    * ``expose_secrets=True`` → real values returned.
    * default → every value is replaced with :data:`REDACTED_SECRET_VALUE`.
    """
    if info.context and info.context.get("cipher"):
        cipher: Cipher = info.context.get("cipher")
        return {
            k: encrypted
            for k, v in d.items()
            if (encrypted := cipher.encrypt(SecretStr(v))) is not None
        }

    if info.context and info.context.get("expose_secrets"):
        return dict(d)

    return dict.fromkeys(d, REDACTED_SECRET_VALUE)


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
