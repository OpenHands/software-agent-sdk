"""Shared helpers for the ``X-Expose-Secrets`` flow used by settings and profiles."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, cast

from fastapi import Request
from pydantic import SecretStr
from pydantic_core import PydanticSerializationError

from openhands.sdk.errors import Conflict, Invalid, Unavailable
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm import LLM_SECRET_FIELDS
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX, Cipher
from openhands.sdk.utils.pydantic_secrets import (
    MissingCipherError,
    SecretDecryptionError,
)


_logger = get_logger(__name__)


ExposeSecretsMode = Literal["encrypted", "plaintext"]


def parse_expose_secrets_header(request: Request) -> ExposeSecretsMode | None:
    """Parse the ``X-Expose-Secrets`` header.

    Returns ``"encrypted"``, ``"plaintext"``, or ``None`` (header absent).
    Raises ``Invalid`` (400) for any other value.
    """
    header_value = request.headers.get("X-Expose-Secrets", "").lower().strip()

    if not header_value:
        return None

    # Legacy alias accepted for the settings flow's pre-existing clients;
    # mapped to "encrypted" so a stale "true" never accidentally exposes plaintext.
    if header_value == "true":
        return "encrypted"

    if header_value in ("encrypted", "plaintext"):
        return cast(ExposeSecretsMode, header_value)

    raise Invalid(
        f"Invalid X-Expose-Secrets header value: '{header_value}'. "
        "Valid values are: 'encrypted', 'plaintext'."
    )


def build_expose_context(
    expose_mode: ExposeSecretsMode | None, cipher: Cipher | None
) -> dict[str, Any]:
    """Build the pydantic serialization context for the given expose mode."""
    if expose_mode is None:
        return {}
    return {"expose_secrets": expose_mode, "cipher": cipher}


def _has_missing_cipher_cause(exc: BaseException) -> bool:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, MissingCipherError):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def decrypt_incoming_llm_secrets(llm: LLM, cipher: Cipher) -> LLM:
    """Decrypt any pre-encrypted LLM secret fields posted back by the client.

    FastAPI parses the request body without a cipher in the validation context,
    so an encrypted blob arrives as ``SecretStr("gAAAAA...")``. Without this
    pass, downstream code (e.g. profile save, ``conversation.switch_llm``) sees
    the encrypted ciphertext as the API key and would either re-encrypt it or
    forward it to the model provider verbatim. Plaintext input is left
    untouched.
    """
    updates: dict[str, SecretStr] = {}
    for field in LLM_SECRET_FIELDS:
        val = getattr(llm, field, None)
        if not isinstance(val, SecretStr):
            continue
        raw = val.get_secret_value()
        if not raw.startswith(FERNET_TOKEN_PREFIX):
            continue
        decrypted = cipher.decrypt(raw)
        if decrypted is not None:
            updates[field] = decrypted
    return llm.model_copy(update=updates) if updates else llm


@contextmanager
def translate_missing_cipher() -> Iterator[None]:
    """Translate a missing-cipher serializer error into ``Unavailable`` (503)."""
    try:
        yield
    except (PydanticSerializationError, MissingCipherError) as e:
        if _has_missing_cipher_cause(e):
            raise Unavailable(
                "Encryption not available: OH_SECRET_KEY is not configured. "
                "Cannot return encrypted secrets."
            )
        raise


def profile_key_mismatch(name: str, error: SecretDecryptionError) -> Conflict:
    """409 for a profile whose stored key the current cipher cannot decrypt."""
    _logger.error(f"Refusing to use profile '{name}': {error}")
    return Conflict(
        f"Profile '{name}' is encrypted with a different key: restore that "
        "key as OH_SECRET_KEY or add it to OH_PREVIOUS_SECRET_KEYS"
    )
