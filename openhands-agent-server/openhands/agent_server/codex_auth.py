import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.secret import LookupSecret


CODEX_AUTH_SECRET_NAME = "CODEX_AUTH_JSON"
CODEX_AUTH_ROUTE_PREFIX = "/api/conversations"
CODEX_AUTH_ROUTE = "/{conversation_id}/codex-auth"

_CODEX_AUTH_HEADER = "X-OH-Codex-Token"
_LOCAL_SECRET_PATH = "/api/settings/secrets/CODEX_AUTH_JSON"
_REFRESH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_REFRESH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_REFRESH_USERNAME = "codex"
_MAX_SECRET_VALUE_LENGTH = 64 * 1024
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def codex_auth_path(conversation_id: UUID) -> str:
    return (
        f"{CODEX_AUTH_ROUTE_PREFIX}"
        f"{CODEX_AUTH_ROUTE.format(conversation_id=conversation_id)}"
    )


def is_chatgpt_codex_auth(value: str) -> bool:
    try:
        document = json.loads(value)
    except (TypeError, ValueError):
        return False
    if not isinstance(document, dict):
        return False
    if document.get("auth_mode") not in (None, "chatgpt"):
        return False
    tokens = document.get("tokens")
    return (
        isinstance(tokens, dict)
        and isinstance(tokens.get("refresh_token"), str)
        and bool(tokens["refresh_token"])
    )


@dataclass
class CodexAuthBroker:
    store: FileSecretsStore
    _capability_digests: dict[UUID, bytes] = field(default_factory=dict, init=False)
    _capability_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def ensure_brokered_source(
        self, conversation_id: UUID, source: LookupSecret
    ) -> LookupSecret:
        parsed_url = urlsplit(source.url)
        path = parsed_url.path.rstrip("/")
        if path != _LOCAL_SECRET_PATH:
            return source

        token = secrets.token_urlsafe(32)
        token_digest = hashlib.sha256(token.encode()).digest()
        with self._capability_lock:
            self._capability_digests[conversation_id] = token_digest
        broker_path = codex_auth_path(conversation_id)
        return LookupSecret(
            url=parsed_url._replace(path=broker_path, query="", fragment="").geturl(),
            headers={_CODEX_AUTH_HEADER: token},
            description=source.description,
        )

    def is_authorized(self, conversation_id: UUID, token: str) -> bool:
        candidate = hashlib.sha256(token.encode()).digest()
        with self._capability_lock:
            expected = self._capability_digests.get(conversation_id)
        return expected is not None and hmac.compare_digest(expected, candidate)

    def revoke(self, conversation_id: UUID, token: str | None = None) -> bool:
        with self._capability_lock:
            expected = self._capability_digests.get(conversation_id)
            if expected is None:
                return False
            if token is not None:
                candidate = hashlib.sha256(token.encode()).digest()
                if not hmac.compare_digest(expected, candidate):
                    return False
            del self._capability_digests[conversation_id]
            return True

    def clear(self) -> None:
        with self._capability_lock:
            self._capability_digests.clear()

    def get_value(self) -> str | None:
        return self.store.get_secret(CODEX_AUTH_SECRET_NAME)

    def compare_and_swap(self, expected_digest: str, value: str) -> bool:
        return self.store.compare_and_swap_secret(
            CODEX_AUTH_SECRET_NAME, expected_digest, value
        )

    @asynccontextmanager
    async def serialized_refresh(self) -> AsyncIterator[None]:
        async with self._refresh_lock:
            yield


def _get_broker(request: Request) -> CodexAuthBroker:
    service = getattr(request.app.state, "conversation_service", None)
    broker = getattr(service, "codex_auth_broker", None)
    if broker is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Codex credential broker is unavailable",
        )
    return broker


def _authorize(
    request: Request, conversation_id: UUID, token: str | None
) -> CodexAuthBroker:
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=f"{_CODEX_AUTH_HEADER} header is required",
        )
    broker = _get_broker(request)
    if not broker.is_authorized(conversation_id, token):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Codex auth token",
        )
    return broker


async def _parse_update(request: Request) -> tuple[str, str]:
    body = await request.body()
    try:
        payload = json.loads(body)
        expected_digest = payload["expected_digest"]
        value = payload["value"]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Codex credential update",
        ) from None
    try:
        value_size = len(value.encode()) if isinstance(value, str) else None
    except UnicodeError:
        value_size = None
    if not (
        isinstance(expected_digest, str)
        and _DIGEST_PATTERN.fullmatch(expected_digest) is not None
        and isinstance(value, str)
        and value_size is not None
        and value_size <= _MAX_SECRET_VALUE_LENGTH
        and is_chatgpt_codex_auth(value)
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Codex credential update",
        )
    return expected_digest, value


def _decode_refresh_authorization(value: str | None) -> str:
    try:
        scheme, encoded = value.split(" ", 1) if value else ("", "")
        decoded = base64.b64decode(encoded, validate=True).decode()
        username, separator, token = decoded.partition(":")
    except (UnicodeError, ValueError):
        scheme = ""
        username = ""
        separator = ""
        token = ""
    if (
        scheme.lower() != "basic"
        or username != _REFRESH_USERNAME
        or not separator
        or not token
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Codex refresh authorization",
        )
    return token


async def _parse_refresh_request(request: Request) -> str:
    body = await request.body()
    try:
        payload = json.loads(body)
        client_id = payload["client_id"]
        grant_type = payload["grant_type"]
        refresh_token = payload["refresh_token"]
    except (KeyError, TypeError, UnicodeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Codex credential refresh",
        ) from None
    if (
        client_id != _REFRESH_CLIENT_ID
        or grant_type != "refresh_token"
        or not isinstance(refresh_token, str)
        or not refresh_token
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Codex credential refresh",
        )
    return refresh_token


def _token_payload(value: str) -> dict[str, str]:
    try:
        tokens = json.loads(value)["tokens"]
    except (KeyError, TypeError, ValueError):
        tokens = None
    if not isinstance(tokens, dict):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stored Codex authentication needs to be refreshed",
        )
    payload = {
        key: token
        for key in ("id_token", "access_token", "refresh_token")
        if isinstance((token := tokens.get(key)), str) and token
    }
    if "refresh_token" not in payload:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stored Codex authentication needs to be refreshed",
        )
    return payload


def _merge_refresh(value: str, refresh: dict[str, Any]) -> str:
    if not isinstance(refresh.get("access_token"), str) or not refresh["access_token"]:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Codex credential refresh returned an invalid response",
        )
    document = json.loads(value)
    tokens = document["tokens"]
    for key in ("id_token", "access_token", "refresh_token"):
        token = refresh.get(key)
        if isinstance(token, str) and token:
            tokens[key] = token
    document["last_refresh"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    updated = json.dumps(document, separators=(",", ":"))
    if not is_chatgpt_codex_auth(updated):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Codex credential refresh returned invalid authentication",
        )
    return updated


async def _request_token_refresh(refresh_token: str) -> httpx.Response:
    url = os.getenv("OPENHANDS_CODEX_REFRESH_TOKEN_URL", _REFRESH_TOKEN_URL)
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            url,
            json={
                "client_id": _REFRESH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )


def _refresh_error(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
    except (AttributeError, TypeError, ValueError):
        code = None
    return {"error": {"code": code or "credential_refresh_failed"}}


router = APIRouter(prefix=CODEX_AUTH_ROUTE_PREFIX)


@router.get(CODEX_AUTH_ROUTE, include_in_schema=False)
async def get_codex_auth(
    conversation_id: UUID,
    request: Request,
    x_oh_codex_token: str | None = Header(None),
) -> Response:
    broker = _authorize(request, conversation_id, x_oh_codex_token)
    value = broker.get_value()
    if value is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Codex credentials were not found"
        )
    if not is_chatgpt_codex_auth(value):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stored Codex authentication needs to be refreshed",
        )
    return Response(
        content=value,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.head(CODEX_AUTH_ROUTE, include_in_schema=False)
async def touch_codex_auth(
    conversation_id: UUID,
    request: Request,
    x_oh_codex_token: str | None = Header(None),
) -> Response:
    broker = _authorize(request, conversation_id, x_oh_codex_token)
    value = broker.get_value()
    if value is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Codex credentials were not found"
        )
    digest = hashlib.sha256(value.encode()).hexdigest()
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"X-Codex-Auth-Digest": digest, "Cache-Control": "no-store"},
    )


@router.put(CODEX_AUTH_ROUTE, include_in_schema=False)
async def update_codex_auth(
    conversation_id: UUID,
    request: Request,
    x_oh_codex_token: str | None = Header(None),
) -> Response:
    broker = _authorize(request, conversation_id, x_oh_codex_token)
    expected_digest, value = await _parse_update(request)
    try:
        updated = broker.compare_and_swap(expected_digest, value)
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Codex credentials were not found"
        ) from exc
    if not updated:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Codex credentials changed in another session.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(f"{CODEX_AUTH_ROUTE}/refresh", include_in_schema=False)
async def refresh_codex_auth(
    conversation_id: UUID,
    request: Request,
    authorization: str | None = Header(None),
) -> Response:
    token = _decode_refresh_authorization(authorization)
    broker = _authorize(request, conversation_id, token)
    submitted_refresh_token = await _parse_refresh_request(request)
    async with broker.serialized_refresh():
        _authorize(request, conversation_id, token)
        current = broker.get_value()
        if current is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Codex credentials were not found"
            )
        current_tokens = _token_payload(current)
        current_refresh_token = current_tokens["refresh_token"]
        if not hmac.compare_digest(
            submitted_refresh_token.encode(errors="surrogatepass"),
            current_refresh_token.encode(errors="surrogatepass"),
        ):
            return JSONResponse(
                current_tokens,
                headers={"Cache-Control": "no-store"},
            )
        try:
            response = await _request_token_refresh(current_refresh_token)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="Codex credential refresh is unavailable",
            ) from exc
        if not response.is_success:
            response_status = (
                response.status_code
                if 400 <= response.status_code < 500
                else status.HTTP_502_BAD_GATEWAY
            )
            return JSONResponse(
                _refresh_error(response),
                status_code=response_status,
                headers={"Cache-Control": "no-store"},
            )
        try:
            refresh = response.json()
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="Codex credential refresh returned an invalid response",
            ) from exc
        if not isinstance(refresh, dict):
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="Codex credential refresh returned an invalid response",
            )
        updated_value = _merge_refresh(current, refresh)
        current_digest = hashlib.sha256(current.encode()).hexdigest()
        try:
            updated = broker.compare_and_swap(current_digest, updated_value)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Codex credentials were not found"
            ) from exc
        if not updated:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Codex credentials changed during refresh",
            )
        return JSONResponse(
            _token_payload(updated_value),
            headers={"Cache-Control": "no-store"},
        )


@router.delete(CODEX_AUTH_ROUTE, include_in_schema=False)
async def release_codex_auth(
    conversation_id: UUID,
    request: Request,
    x_oh_codex_token: str | None = Header(None),
) -> Response:
    broker = _authorize(request, conversation_id, x_oh_codex_token)
    broker.revoke(conversation_id, x_oh_codex_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
