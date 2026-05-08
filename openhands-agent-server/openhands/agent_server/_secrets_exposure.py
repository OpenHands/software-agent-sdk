"""Shared helpers for the ``X-Expose-Secrets`` flow used by settings and profiles."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, cast

from fastapi import HTTPException, Request, status
from pydantic_core import PydanticSerializationError

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import MissingCipherError


ExposeSecretsMode = Literal["encrypted", "plaintext"]


def get_config(request: Request):
    """Get config from app state, raising 503 if uninitialized."""
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server not fully initialized",
        )
    return config


def get_cipher(request: Request) -> Cipher | None:
    """Get the configured cipher (``None`` when ``OH_SECRET_KEY`` is unset)."""
    return get_config(request).cipher


def parse_expose_secrets_header(request: Request) -> ExposeSecretsMode | None:
    """Parse the ``X-Expose-Secrets`` header.

    Returns ``"encrypted"``, ``"plaintext"``, or ``None`` (header absent).
    Raises ``HTTPException(400)`` for any other value.
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

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"Invalid X-Expose-Secrets header value: '{header_value}'. "
            "Valid values are: 'encrypted', 'plaintext'."
        ),
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


@contextmanager
def translate_missing_cipher() -> Iterator[None]:
    """Translate a missing-cipher serializer error into HTTP 503."""
    try:
        yield
    except (PydanticSerializationError, MissingCipherError) as e:
        if _has_missing_cipher_cause(e):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Encryption not available: OH_SECRET_KEY is not configured. "
                    "Cannot return encrypted secrets."
                ),
            )
        raise
