import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Sequence
from typing import TypeGuard
from uuid import UUID


CONVERSATION_TOKEN_PREFIX = "oh1."


class InvalidConversationTokenError(ValueError):
    pass


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(key: str, signed_part: str) -> str:
    digest = hmac.new(
        key.encode("utf-8"), signed_part.encode("utf-8"), hashlib.sha256
    ).digest()
    return _b64encode(digest)


def is_conversation_token(credential: str | None) -> TypeGuard[str]:
    return credential is not None and credential.startswith(CONVERSATION_TOKEN_PREFIX)


def mint_conversation_token(key: str, conversation_id: UUID | str, ttl: float) -> str:
    """Mint a token that authorizes one conversation for ``ttl`` seconds.

    The token is signed with ``key``, which must be one of the agent-server's
    configured ``session_api_keys``.
    """
    if not key:
        raise ValueError("key must be a non-empty session API key")
    if ttl <= 0:
        raise ValueError("ttl must be positive")
    payload = {"cid": str(UUID(str(conversation_id))), "exp": int(time.time() + ttl)}
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signed_part = f"{CONVERSATION_TOKEN_PREFIX}{encoded}"
    return f"{signed_part}.{_sign(key, signed_part)}"


def verify_conversation_token(token: str, keys: Sequence[str]) -> UUID:
    """Return the conversation id a valid, unexpired token is bound to.

    The signature may come from any of ``keys``. Raises
    ``InvalidConversationTokenError`` for a malformed, tampered, expired or
    unknown-key token.
    """
    if not is_conversation_token(token):
        raise InvalidConversationTokenError("not a conversation token")
    signed_part, _, signature = token.rpartition(".")
    if signed_part.count(".") != 1 or not signature:
        raise InvalidConversationTokenError("malformed conversation token")
    if not any(
        key
        and hmac.compare_digest(
            _sign(key, signed_part).encode("ascii"), signature.encode("utf-8")
        )
        for key in keys
    ):
        raise InvalidConversationTokenError("invalid conversation token signature")
    try:
        payload = json.loads(
            _b64decode(signed_part.removeprefix(CONVERSATION_TOKEN_PREFIX))
        )
        conversation_id = UUID(payload["cid"])
        expires_at = payload["exp"]
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise InvalidConversationTokenError("malformed conversation token") from None
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise InvalidConversationTokenError("malformed conversation token")
    if time.time() >= expires_at:
        raise InvalidConversationTokenError("expired conversation token")
    return conversation_id
