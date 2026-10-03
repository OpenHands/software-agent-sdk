"""Server-owned ChatGPT device login and versioned Codex credentials."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from fastapi import APIRouter, HTTPException, Request
from pydantic import (
    BaseModel,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from openhands.agent_server.persistence import FileSecretsStore, get_secrets_store
from openhands.sdk.agent.acp_file_credentials import (
    CODEX_AUTH_SECRET_NAME,
    codex_auth_file,
)
from openhands.sdk.llm.auth.openai import (
    DEVICE_CODE_TIMEOUT_SECONDS,
    ISSUER,
    DeviceCode,
    _exchange_code_for_tokens,
    _poll_device_code_once,
    _refresh_access_token,
    _request_device_code,
)
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


class CodexTokens(BaseModel):
    id_token: SecretStr
    access_token: SecretStr
    refresh_token: SecretStr
    account_id: str | None = None

    @field_validator("id_token", "access_token", "refresh_token")
    @classmethod
    def _validate_token(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        validated = validate_secret(value, info)
        if validated is None:
            raise ValueError("Missing Codex credential")
        return validated

    @model_validator(mode="after")
    def _select_account(self) -> Self:
        claims = _token_claims(self.id_token)
        if claims is None:
            raise ValueError("Invalid Codex ID credential")
        auth = claims.get("https://api.openai.com/auth")
        if self.account_id is None and isinstance(auth, dict):
            account_id = auth.get("chatgpt_account_id")
            if isinstance(account_id, str) and account_id:
                self.account_id = account_id
        return self

    _serialize_tokens = field_serializer("id_token", "access_token", "refresh_token")(
        serialize_secret
    )


class CodexAuthFile(BaseModel):
    auth_mode: Literal["chatgpt"] = "chatgpt"
    tokens: CodexTokens
    last_refresh: str | None = None

    def plaintext(self) -> str:
        return self.model_dump_json(
            exclude_none=True, context={"expose_secrets": "plaintext"}
        )


class CodexAuthStatus(BaseModel):
    connected: bool = False
    state: Literal[
        "disconnected", "pending", "connected", "cancelled", "expired", "error"
    ] = "disconnected"
    expires_at: int | None = None


class CodexDeviceChallenge(BaseModel):
    device_code: str  # Opaque handle; never OpenAI's device authorization secret.
    user_code: str
    verification_uri: str
    expires_at: int
    interval_seconds: int


class CodexDeviceRequest(BaseModel):
    device_code: str


class CodexOAuthProvider(Protocol):
    async def start(self) -> DeviceCode: ...
    async def poll(self, challenge: DeviceCode) -> dict[str, Any] | None: ...
    async def refresh(self, refresh_token: str) -> dict[str, Any]: ...


class OpenAICodexOAuthProvider:
    """Reuse the SDK's OpenAI transport, retaining Codex's ID token."""

    async def start(self) -> DeviceCode:
        return await _request_device_code()

    async def poll(self, challenge: DeviceCode) -> dict[str, Any] | None:
        result = await _poll_device_code_once(challenge)
        if result is None:
            return None
        return await _exchange_code_for_tokens(
            result["authorization_code"],
            f"{ISSUER}/deviceauth/callback",
            result["code_verifier"],
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await _refresh_access_token(refresh_token)


def _token_claims(token: SecretStr) -> dict[str, Any] | None:
    # Metadata from an already acquired token, not signature verification.
    try:
        parts = token.get_secret_value().split(".")
        if len(parts) != 3 or not all(parts):
            return None
        payload = parts[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        return claims if isinstance(claims, dict) else None
    except (ValueError, IndexError, TypeError):
        return None


def _expiration(token: SecretStr) -> int | None:
    claims = _token_claims(token)
    expiration = claims.get("exp") if claims else None
    return int(expiration) * 1000 if isinstance(expiration, (int, float)) else None


@dataclass
class _PendingLogin:
    handle: str
    challenge: DeviceCode = field(repr=False)
    expires_at: int
    epoch: int
    version: str | None


class CodexAuthService:
    def __init__(
        self,
        store: FileSecretsStore,
        host_auth_file: Path,
        provider: CodexOAuthProvider | None = None,
    ):
        self.store = store
        self.host_auth_file = host_auth_file
        self.provider = provider or OpenAICodexOAuthProvider()
        self._lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._epoch = 0
        self._pending: _PendingLogin | None = None
        self._poll_task: asyncio.Task[dict[str, Any] | None] | None = None
        self._refresh_task: asyncio.Task[dict[str, Any]] | None = None

    async def _load(self) -> tuple[CodexAuthFile, str] | None:
        try:
            value, version = await asyncio.to_thread(
                self.store.load_versioned_secret, CODEX_AUTH_SECRET_NAME
            )
            return CodexAuthFile.model_validate_json(value), version
        except (KeyError, ValidationError):
            return None

    async def _fresh_status(self) -> CodexAuthStatus:
        current = await self._load()
        expiration = _expiration(current[0].tokens.access_token) if current else None
        if expiration is not None and expiration > int(time.time() * 1000) + 60_000:
            return CodexAuthStatus(
                connected=True, state="connected", expires_at=expiration
            )
        return CodexAuthStatus()

    def _stop_login(self) -> None:
        self._epoch += 1
        self._pending = None
        if self._poll_task is not None:
            self._poll_task.cancel()

    async def start(self) -> CodexDeviceChallenge:
        async with self._lock:
            self._stop_login()
            epoch = self._epoch
            try:
                _, version = await asyncio.to_thread(
                    self.store.load_versioned_secret, CODEX_AUTH_SECRET_NAME
                )
            except KeyError:
                version = None
        challenge = await self.provider.start()
        expires_at = int(time.time() * 1000) + DEVICE_CODE_TIMEOUT_SECONDS * 1000
        handle = secrets.token_urlsafe(32)
        async with self._lock:
            if epoch == self._epoch:
                self._pending = _PendingLogin(
                    handle, challenge, expires_at, epoch, version
                )
        return CodexDeviceChallenge(
            device_code=handle,
            user_code=challenge.user_code,
            verification_uri=challenge.verification_url,
            expires_at=expires_at,
            interval_seconds=challenge.interval,
        )

    async def poll(self, handle: str) -> CodexAuthStatus:
        async with self._lock:
            pending = self._pending
            if pending is None or not secrets.compare_digest(handle, pending.handle):
                return CodexAuthStatus(state="cancelled")
            if pending.expires_at <= int(time.time() * 1000):
                self._stop_login()
                return CodexAuthStatus(state="expired")
            if self._poll_task is not None and not self._poll_task.done():
                return CodexAuthStatus(state="pending")
            task = asyncio.create_task(self.provider.poll(pending.challenge))
            self._poll_task = task
        try:
            result = await task
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            return CodexAuthStatus(state="cancelled")
        except Exception:
            async with self._lock:
                if pending is self._pending:
                    self._stop_login()
            return CodexAuthStatus(state="error")
        async with self._lock:
            if pending is not self._pending or pending.epoch != self._epoch:
                return CodexAuthStatus(state="cancelled")
            if pending.expires_at <= int(time.time() * 1000):
                self._stop_login()
                return CodexAuthStatus(state="expired")
            if result is None:
                return CodexAuthStatus(state="pending")
            try:
                auth = CodexAuthFile(
                    tokens=CodexTokens.model_validate(result),
                    last_refresh=datetime.now(UTC).isoformat(),
                )
                await asyncio.to_thread(
                    self.store.update_versioned_secret,
                    CODEX_AUTH_SECRET_NAME,
                    pending.version,
                    auth.plaintext(),
                )
            except (ValidationError, ValueError, KeyError):
                self._stop_login()
                return CodexAuthStatus(state="error")
            self._stop_login()
            return CodexAuthStatus(
                connected=True,
                state="connected",
                expires_at=_expiration(auth.tokens.access_token),
            )

    async def cancel(self, handle: str) -> CodexAuthStatus:
        async with self._lock:
            if self._pending is not None and secrets.compare_digest(
                handle, self._pending.handle
            ):
                self._stop_login()
        return CodexAuthStatus(state="cancelled")

    async def _import_host_login(self) -> None:
        try:
            auth = await asyncio.to_thread(
                lambda: CodexAuthFile.model_validate_json(
                    self.host_auth_file.read_text()
                )
            )
            await asyncio.to_thread(
                self.store.update_versioned_secret,
                CODEX_AUTH_SECRET_NAME,
                None,
                auth.plaintext(),
            )
        except (OSError, ValidationError, ValueError):
            pass

    async def status(self) -> CodexAuthStatus:
        async with self._refresh_lock:
            async with self._lock:
                current = await self._load()
                if current is None:
                    await self._import_host_login()
                    current = await self._load()
            if current is None:
                return CodexAuthStatus()
            auth, version = current
            expiration = _expiration(auth.tokens.access_token)
            if expiration is not None and expiration > int(time.time() * 1000) + 60_000:
                return CodexAuthStatus(
                    connected=True, state="connected", expires_at=expiration
                )
            task = asyncio.create_task(
                self.provider.refresh(auth.tokens.refresh_token.get_secret_value())
            )
            self._refresh_task = task
            try:
                result = await task
                updated = CodexAuthFile(
                    tokens=CodexTokens.model_validate(
                        {
                            **auth.tokens.model_dump(
                                context={"expose_secrets": "plaintext"}
                            ),
                            **result,
                        }
                    ),
                    last_refresh=datetime.now(UTC).isoformat(),
                )
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    raise
                return CodexAuthStatus()
            except Exception:
                try:
                    await asyncio.to_thread(
                        self.store.update_versioned_secret,
                        CODEX_AUTH_SECRET_NAME,
                        version,
                        None,
                    )
                except ValueError:
                    return await self._fresh_status()
                return CodexAuthStatus()
            finally:
                self._refresh_task = None
            try:
                await asyncio.to_thread(
                    self.store.update_versioned_secret,
                    CODEX_AUTH_SECRET_NAME,
                    version,
                    updated.plaintext(),
                )
            except ValueError:
                # The ACP subprocess or another login won the version race.
                return await self._fresh_status()
            return CodexAuthStatus(
                connected=True,
                state="connected",
                expires_at=_expiration(updated.tokens.access_token),
            )

    async def logout(self) -> CodexAuthStatus:
        async with self._lock:
            self._stop_login()
            if self._refresh_task is not None:
                self._refresh_task.cancel()
            await asyncio.to_thread(self.store.delete_secret, CODEX_AUTH_SECRET_NAME)
            try:
                # Preserve API-key auth files; disconnect applies to ChatGPT.
                await asyncio.to_thread(
                    lambda: CodexAuthFile.model_validate_json(
                        self.host_auth_file.read_text()
                    )
                )
                await asyncio.to_thread(self.host_auth_file.unlink)
            except (OSError, ValidationError):
                pass
        return CodexAuthStatus()


codex_auth_router = APIRouter(prefix="/acp/codex/auth", tags=["Codex authentication"])


def _service(request: Request) -> CodexAuthService:
    if request.app.state.codex_auth is None:
        request.app.state.codex_auth = CodexAuthService(
            get_secrets_store(request.app.state.config),
            codex_auth_file(dict(os.environ)),
        )
    return request.app.state.codex_auth


@codex_auth_router.get("/status", response_model=CodexAuthStatus)
async def codex_auth_status(request: Request) -> CodexAuthStatus:
    return await _service(request).status()


@codex_auth_router.post("/device/start", response_model=CodexDeviceChallenge)
async def codex_auth_start(request: Request) -> CodexDeviceChallenge:
    try:
        return await _service(request).start()
    except Exception:
        raise HTTPException(
            502,
            "Could not start ChatGPT sign-in. "
            "Enable device-code login in ChatGPT and try again.",
        ) from None


@codex_auth_router.post("/device/poll", response_model=CodexAuthStatus)
async def codex_auth_poll(
    body: CodexDeviceRequest, request: Request
) -> CodexAuthStatus:
    return await _service(request).poll(body.device_code)


@codex_auth_router.post("/device/cancel", response_model=CodexAuthStatus)
async def codex_auth_cancel(
    body: CodexDeviceRequest, request: Request
) -> CodexAuthStatus:
    return await _service(request).cancel(body.device_code)


@codex_auth_router.post("/logout", response_model=CodexAuthStatus)
async def codex_auth_logout(request: Request) -> CodexAuthStatus:
    return await _service(request).logout()
