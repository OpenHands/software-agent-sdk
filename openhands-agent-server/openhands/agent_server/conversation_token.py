"""Short-lived credentials that reach one conversation on a shared server.

A conversation token is ``oh1.<payload>.<signature>``: a base64url JSON payload
``{"cid": "<conversation UUID>", "exp": <unix seconds>}`` and its HMAC-SHA256,
keyed with one of the server's ``session_api_keys``. Rotating those keys
revokes every outstanding token. Tokens travel where session keys do (the
``X-Session-API-Key`` header and the WebSocket auth frame) but only authorize
the routes of their own conversation; see :func:`conversation_token_allows`.

This module is stdlib-only so orchestrators can mint tokens with the same code
the server verifies them with.
"""

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import time
from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID


CONVERSATION_TOKEN_PREFIX = "oh1."

_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")

# Route templates a token may reach, after the server's own prefixes:
# ``/api/conversations/{id}[/...]`` and the per-conversation sockets. The
# optional ``/_docker`` segment is ``docker_runtime.hybrid.DOCKER_ROUTE_PREFIX``.
_SCOPED_ROUTE = re.compile(
    r"^(?:/api(?:/_docker)?/conversations|(?:/_docker)?/sockets/(?:events|session))"
    r"/\{(?:conversation_id|runtime_conversation_id)\}(?P<rest>(?:/.*)?)$"
)
_CONVERSATION_ID_PARAMS = ("conversation_id", "runtime_conversation_id")
_PATH_PARAM = re.compile(r"\{(\w+):path\}")

# Direct host shell, the host IDE, and the Docker runtime's own session key.
# Refused even under the token's own conversation. ``*`` matches one segment.
_REFUSED_ROUTES: tuple[tuple[str, ...], ...] = (
    ("bash", "execute_bash_command"),
    ("bash", "start_bash_command"),
    ("bash", "bash_commands", "*", "stop"),
    ("vscode",),
    ("runtime", "credentials"),
)


def mint_conversation_token(
    key: str, conversation_id: UUID | str, ttl: timedelta
) -> str:
    """Return a token for ``conversation_id`` that expires after ``ttl``.

    ``key`` must be one of the target server's ``session_api_keys``.
    """
    if not key:
        raise ValueError("key must be a non-empty session API key")
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")
    claims = {
        "cid": str(UUID(str(conversation_id))),
        "exp": math.ceil(time.time() + ttl.total_seconds()),
    }
    payload = _b64encode(json.dumps(claims, separators=(",", ":")).encode())
    return f"{CONVERSATION_TOKEN_PREFIX}{payload}.{_b64encode(_sign(key, payload))}"


def verify_conversation_token(token: str, keys: Iterable[str]) -> UUID | None:
    """Return the token's conversation ID, or ``None`` if the token is invalid.

    A token is valid when it is well formed, unexpired, and signed by any of
    ``keys``.
    """
    if not is_conversation_token(token):
        return None
    payload, _, signature = token.removeprefix(CONVERSATION_TOKEN_PREFIX).partition(".")
    signature_bytes = _b64decode(signature)
    if signature_bytes is None or not any(
        hmac.compare_digest(_sign(key, payload), signature_bytes) for key in keys if key
    ):
        return None
    payload_bytes = _b64decode(payload)
    if payload_bytes is None:
        return None
    try:
        claims = json.loads(payload_bytes)
    except ValueError:
        return None
    if not isinstance(claims, dict):
        return None
    cid, exp = claims.get("cid"), claims.get("exp")
    if not isinstance(cid, str) or type(exp) is not int or exp <= time.time():
        return None
    try:
        return UUID(cid)
    except ValueError:
        return None


def is_conversation_token(value: str) -> bool:
    return value.startswith(CONVERSATION_TOKEN_PREFIX)


def conversation_token_allows(
    route_path: str, path_params: Mapping[str, Any], conversation_id: UUID
) -> bool:
    """Whether a token for ``conversation_id`` may use the matched route.

    ``route_path`` is the matched route template, so collection routes such as
    ``/api/conversations/search`` never read as a conversation ID. Every
    conversation ID path parameter must equal the token's.
    """
    match = _SCOPED_ROUTE.match(route_path)
    if match is None:
        return False
    for name in _CONVERSATION_ID_PARAMS:
        if name in path_params and not _is_conversation(
            path_params[name], conversation_id
        ):
            return False
    segments = _route_segments(match["rest"], path_params)
    return segments is not None and not any(
        _starts_with(segments, refused) for refused in _REFUSED_ROUTES
    )


def _route_segments(rest: str, path_params: Mapping[str, Any]) -> list[str] | None:
    """Split the route after the conversation ID into segments.

    ``{name:path}`` parameters are expanded with the request's value because
    the Docker runtime proxies its catch-all tail to the inner server, which
    routes it again. Values the inner request could resolve differently
    (dot segments, percent escapes, backslashes) are refused outright.
    """
    segments: list[str] = []
    for segment in rest.split("/"):
        path_param = _PATH_PARAM.fullmatch(segment)
        if path_param is None:
            parts = [segment]
        else:
            value = str(path_params.get(path_param[1], ""))
            if "%" in value or "\\" in value:
                return None
            parts = value.split("/")
        for part in parts:
            if part == "..":
                return None
            if part not in ("", "."):
                segments.append(part)
    return segments


def _starts_with(segments: list[str], pattern: tuple[str, ...]) -> bool:
    return len(segments) >= len(pattern) and all(
        expected == "*" or expected == actual
        for expected, actual in zip(pattern, segments, strict=False)
    )


def _is_conversation(value: Any, conversation_id: UUID) -> bool:
    try:
        return UUID(str(value)) == conversation_id
    except ValueError:
        return False


def _sign(key: str, payload: str) -> bytes:
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(segment: str) -> bytes | None:
    if not _BASE64URL.fullmatch(segment):
        return None
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        return None
