"""Authenticated browser bridge for owned Canvas App backends."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Protocol
from urllib.parse import unquote, urlparse

from fastapi import HTTPException, Request, Response, WebSocket, status
from pydantic import BaseModel

from openhands.agent_server.auth_router import (
    _append_partitioned_to_last_set_cookie,
    _request_is_secure_context,
)
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.proxy import (
    bridge_websocket,
    proxy_http,
    strip_auth_query,
)


APP_BACKEND_SESSION_COOKIE_NAME: Final = "oh_app_backend_session"
APP_BACKEND_SESSION_TTL_SECONDS: Final = 5 * 60
_APP_BACKEND_PATH: Final = "/app-backends"
_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


class BackendRegistry(Protocol):
    def ready_endpoint(self, name: str) -> tuple[str, int] | None: ...


class AppBackendSessionResponse(BaseModel):
    """Browser bootstrap result for one running Canvas App backend.

    The session credential is delivered only as an HttpOnly cookie. ``ingress_url``
    contains no credential and must be loaded from a separate browser origin. That
    origin is the isolation boundary, so frames retain their origin for cookies,
    workers, WebSockets, and Origin-based request validation.
    """

    ingress_url: str
    expires_at: datetime
    iframe_sandbox: str = (
        "allow-forms allow-modals allow-popups allow-same-origin allow-scripts"
    )


@dataclass(frozen=True, slots=True)
class _BackendTarget:
    host: str
    api_key: str | None = None


@dataclass(slots=True)
class _AppBackendSession:
    token_digest: bytes
    extension_name: str
    endpoint: tuple[str, int]
    expires_at: float
    sockets: set[asyncio.Task[None]] = field(default_factory=set)


class AppBackendSessionStore:
    """Own short-lived app sessions and their live WebSocket bridges."""

    def __init__(self, ttl_seconds: int = APP_BACKEND_SESSION_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[bytes, _AppBackendSession] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    async def create(
        self, extension_name: str, endpoint: tuple[str, int]
    ) -> tuple[str, _AppBackendSession]:
        token = secrets.token_urlsafe(32)
        session = _AppBackendSession(
            token_digest=self._digest(token),
            extension_name=extension_name,
            endpoint=endpoint,
            expires_at=time.time() + self.ttl_seconds,
        )
        async with self._lock:
            self._prune_expired_locked()
            self._sessions[session.token_digest] = session
        return token, session

    async def authorize(
        self, token: str | None, extension_name: str, endpoint: tuple[str, int]
    ) -> _AppBackendSession | None:
        if not token:
            return None
        digest = self._digest(token)
        async with self._lock:
            self._prune_expired_locked()
            session = self._sessions.get(digest)
            if session is None or not hmac.compare_digest(session.token_digest, digest):
                return None
            if session.extension_name != extension_name or session.endpoint != endpoint:
                return None
            return session

    async def attach_socket(
        self, session: _AppBackendSession, task: asyncio.Task[None]
    ) -> None:
        async with self._lock:
            if self._sessions.get(session.token_digest) is not session:
                raise RuntimeError("App backend session was revoked")
            session.sockets.add(task)

    async def detach_socket(
        self, session: _AppBackendSession, task: asyncio.Task[None]
    ) -> None:
        async with self._lock:
            session.sockets.discard(task)

    async def revoke_token(self, token: str | None) -> None:
        if not token:
            return
        await self._revoke_digests({self._digest(token)})

    async def revoke_app(self, extension_name: str) -> None:
        async with self._lock:
            digests = {
                digest
                for digest, session in self._sessions.items()
                if session.extension_name == extension_name
            }
        await self._revoke_digests(digests)

    async def shutdown(self) -> None:
        async with self._lock:
            digests = set(self._sessions)
        await self._revoke_digests(digests)

    async def _revoke_digests(self, digests: set[bytes]) -> None:
        async with self._lock:
            sessions = [
                self._sessions.pop(digest)
                for digest in digests
                if digest in self._sessions
            ]
        tasks = {
            task for session in sessions for task in session.sockets if not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _prune_expired_locked(self) -> None:
        now = time.time()
        for digest, session in list(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(digest)
                for task in session.sockets:
                    task.cancel()


def get_app_backend_session_store(
    request: Request | WebSocket,
) -> AppBackendSessionStore:
    store = getattr(request.app.state, "app_backend_session_store", None)
    if store is None:
        store = AppBackendSessionStore()
        request.app.state.app_backend_session_store = store
    return store


def _public_url(config: Config) -> str:
    value = config.app_backend_public_url
    if not value:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Canvas App backend ingress is not configured",
        )
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Canvas App backend ingress is misconfigured",
        )
    return value.rstrip("/")


def _origin(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"


def _allowed_control_origin(config: Config, origin: str) -> bool:
    normalized = _origin(origin)
    if normalized is None:
        return False
    parsed = urlparse(origin)
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        return True
    docker_host = os.environ.get("DOCKER_HOST_ADDR")
    if docker_host and parsed.hostname == docker_host:
        return True
    if any(_origin(candidate) == normalized for candidate in config.allow_cors_origins):
        return True
    return bool(
        config.allow_cors_origin_regex
        and re.fullmatch(config.allow_cors_origin_regex, origin)
    )


def _request_origin(request: Request | WebSocket) -> str | None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = forwarded_proto.split(",")[0].strip() or request.url.scheme
    scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    forwarded_host = request.headers.get("x-forwarded-host", "")
    host = forwarded_host.split(",")[0].strip() or request.headers.get("host", "")
    return _origin(f"{scheme}://{host}")


def _require_ingress_host(request: Request | WebSocket, public_url: str) -> None:
    if _request_origin(request) != _origin(public_url):
        raise HTTPException(
            status.HTTP_421_MISDIRECTED_REQUEST,
            "Canvas App request must use the configured ingress origin",
        )


def _require_bootstrap_origin(request: Request, public_url: str) -> None:
    _require_ingress_host(request, public_url)
    origin = request.headers.get("origin", "")
    config: Config = request.app.state.config
    if not _allowed_control_origin(config, origin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin is not allowed")
    if _origin(origin) == _origin(public_url):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Canvas App ingress must use a separate browser origin",
        )


def _require_app_origin(origin: str | None, public_url: str) -> None:
    if not origin or _origin(origin) != _origin(public_url):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin is not allowed")


def _validate_proxy_path(path: str) -> None:
    candidate = path
    for _ in range(3):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    if "\\" in candidate or any(part in {".", ".."} for part in candidate.split("/")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid backend path")


def _ready_endpoint(
    request: Request | WebSocket, extension_name: str
) -> tuple[str, int]:
    manager: BackendRegistry | None = getattr(
        request.app.state, "canvas_extension_backend_manager", None
    )
    endpoint = manager.ready_endpoint(extension_name) if manager is not None else None
    if endpoint is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Canvas App backend is not ready",
        )
    host, port = endpoint
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Canvas App backend target is not loopback",
        )
    return host, port


def _target(endpoint: tuple[str, int]) -> _BackendTarget:
    host, port = endpoint
    bracketed_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return _BackendTarget(host=f"http://{bracketed_host}:{port}")


def _cookie_path(extension_name: str) -> str:
    return f"{_APP_BACKEND_PATH}/{extension_name}"


def _set_session_cookie(
    response: Response,
    request: Request,
    extension_name: str,
    token: str,
    max_age: int,
) -> None:
    secure = _request_is_secure_context(request)
    if not secure:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Canvas App sessions require HTTPS or a loopback secure context",
        )
    response.set_cookie(
        key=APP_BACKEND_SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        path=_cookie_path(extension_name),
        secure=True,
        httponly=True,
        samesite="none",
    )
    _append_partitioned_to_last_set_cookie(response, APP_BACKEND_SESSION_COOKIE_NAME)


async def create_app_backend_session(
    request: Request, response: Response, extension_name: str
) -> AppBackendSessionResponse:
    public_url = _public_url(request.app.state.config)
    _require_bootstrap_origin(request, public_url)
    endpoint = _ready_endpoint(request, extension_name)
    token, session = await get_app_backend_session_store(request).create(
        extension_name, endpoint
    )
    _set_session_cookie(
        response,
        request,
        extension_name,
        token,
        APP_BACKEND_SESSION_TTL_SECONDS,
    )
    return AppBackendSessionResponse(
        ingress_url=f"{public_url}{_cookie_path(extension_name)}/",
        expires_at=datetime.fromtimestamp(session.expires_at, UTC),
    )


async def delete_app_backend_session(
    request: Request, response: Response, extension_name: str
) -> None:
    public_url = _public_url(request.app.state.config)
    _require_bootstrap_origin(request, public_url)
    token = request.cookies.get(APP_BACKEND_SESSION_COOKIE_NAME)
    await get_app_backend_session_store(request).revoke_token(token)
    _set_session_cookie(response, request, extension_name, "", 0)


async def proxy_app_backend_http(
    request: Request, extension_name: str, path: str
) -> Response:
    _validate_proxy_path(path)
    public_url = _public_url(request.app.state.config)
    _require_ingress_host(request, public_url)
    if request.method not in _SAFE_METHODS:
        _require_app_origin(request.headers.get("origin"), public_url)
    endpoint = _ready_endpoint(request, extension_name)
    token = request.cookies.get(APP_BACKEND_SESSION_COOKIE_NAME)
    session = await get_app_backend_session_store(request).authorize(
        token, extension_name, endpoint
    )
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    query = strip_auth_query("?" + request.url.query).lstrip("?")
    upstream_path = f"/{path}" if path else "/"
    if query:
        upstream_path = f"{upstream_path}?{query}"
    return await proxy_http(
        request,
        _target(endpoint),
        upstream_path=upstream_path,
        reject_redirects=True,
    )


async def proxy_app_backend_websocket(
    websocket: WebSocket, extension_name: str, path: str
) -> None:
    try:
        _validate_proxy_path(path)
        public_url = _public_url(websocket.app.state.config)
        _require_ingress_host(websocket, public_url)
        _require_app_origin(websocket.headers.get("origin"), public_url)
        endpoint = _ready_endpoint(websocket, extension_name)
        token = websocket.cookies.get(APP_BACKEND_SESSION_COOKIE_NAME)
        store = get_app_backend_session_store(websocket)
        session = await store.authorize(token, extension_name, endpoint)
        if session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    except HTTPException:
        await websocket.close(code=1008)
        return

    task = asyncio.current_task()
    assert task is not None
    try:
        await store.attach_socket(session, task)
    except RuntimeError:
        await websocket.close(code=1008)
        return

    query = strip_auth_query("?" + websocket.url.query).lstrip("?")
    upstream_path = f"/{path}" if path else "/"
    if query:
        upstream_path = f"{upstream_path}?{query}"
    await websocket.accept()
    try:
        await bridge_websocket(
            websocket,
            _target(endpoint),
            upstream_path=upstream_path,
        )
    finally:
        await store.detach_socket(session, task)
