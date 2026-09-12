"""Per-runtime Unix socket with grants fixed by the outer control manifest."""

from __future__ import annotations

import asyncio
import hmac
import tempfile
from contextlib import contextmanager, suppress
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastmcp.client.auth import OAuth
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from openhands.agent_server.credential_binding import LocalVersionedCredentialBinding
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.mcp_oauth_store import MCPSettingsOAuthTokenStore
from openhands.agent_server.persistence import FileSecretsStore, get_settings_store
from openhands.sdk.agent.acp_file_credentials import supports_file_credential_binding
from openhands.sdk.credential import CredentialConflict, CredentialNeedsReauthentication
from openhands.sdk.llm.auth.credentials import CredentialStore, OAuthCredentials
from openhands.sdk.llm.auth.openai import OpenAISubscriptionAuth
from openhands.sdk.mcp.config import MCPOAuthAuthCredential


class CredentialReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str
    value: str = Field(max_length=262144)


class _SocketServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        yield


class RuntimeCredentialBroker:
    def __init__(
        self,
        store: RuntimeProvisioningStore,
        conversation_id: UUID,
        subscription_store: CredentialStore,
        secrets_store: FileSecretsStore,
    ) -> None:
        self.store = store
        self.conversation_id = conversation_id
        self.subscription_store = subscription_store
        self.secrets_store = secrets_store
        self._socket_dir = Path(tempfile.mkdtemp(prefix="oh-credentials-"))
        self._server: _SocketServer | None = None
        self._task: asyncio.Task | None = None
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self._register_routes()
        slots = asyncio.Semaphore(8)

        @self.app.middleware("http")
        async def bounded_request(request, call_next):
            if len(await request.body()) > 300000:
                return JSONResponse(
                    {"detail": "Credential request too large"}, status_code=413
                )
            async with slots:
                return await call_next(request)

    @property
    def socket_dir(self) -> Path:
        return self._socket_dir

    def _register_routes(self) -> None:
        def authorize(authorization: str = Header(default="")):
            try:
                identity = self.store.load(self.conversation_id)
            except (ValueError, OSError) as exc:
                raise HTTPException(401) from exc
            expected = "Bearer " + identity.broker_token.get_secret_value()
            if not hmac.compare_digest(authorization, expected):
                raise HTTPException(401)
            return identity.grants

        @self.app.get("/subscription/openai")
        async def subscription(grants=Depends(authorize)) -> OAuthCredentials:
            if grants.subscription != "openai":
                raise HTTPException(403)

            def refresh() -> OAuthCredentials:
                lock_path = (
                    self.subscription_store.credentials_dir / ".runtime-refresh.lock"
                )
                with FileLock(str(lock_path)):
                    credentials = OpenAISubscriptionAuth(
                        credential_store=self.subscription_store
                    ).refresh_if_needed_sync()
                    if credentials is None:
                        raise HTTPException(409, "Subscription login is required")
                    return credentials.model_copy(update={"refresh_token": ""})

            return await asyncio.to_thread(refresh)

        @self.app.get("/mcp/{name}")
        async def mcp_token(name: str, grants=Depends(authorize)):
            if name not in grants.mcp_server_names:
                raise HTTPException(403)
            lock = FileLock(
                str(
                    self.store.control_root
                    / ("mcp-" + sha256(name.encode()).hexdigest() + ".lock")
                ),
                thread_local=False,
            )
            await asyncio.to_thread(lock.acquire, timeout=30)
            try:
                settings = await asyncio.to_thread(
                    get_settings_store(self.store.config).load
                )
                server = (
                    settings.agent_settings.mcp_config.get(name) if settings else None
                )
                if (
                    server is None
                    or not server.url
                    or not isinstance(server.auth, MCPOAuthAuthCredential)
                ):
                    raise HTTPException(409, "Selected MCP login is unavailable")
                oauth = OAuth(
                    mcp_url=server.url, token_storage=MCPSettingsOAuthTokenStore()
                )
                await oauth._initialize()
                if not oauth.context.is_token_valid():
                    if not oauth.context.can_refresh_token():
                        raise HTTPException(409, "Selected MCP login is required")
                    async with httpx.AsyncClient(timeout=30) as client:
                        response = await client.send(await oauth._refresh_token())
                    if not await oauth._handle_refresh_response(response):
                        raise HTTPException(409, "Selected MCP login must be renewed")
                tokens = oauth.context.current_tokens
                if tokens is None:
                    raise HTTPException(409, "Selected MCP login is required")
                return {"access_token": tokens.access_token}
            finally:
                await asyncio.to_thread(lock.release)

        @self.app.get("/credential/{name}")
        async def load_credential(name: str, grants=Depends(authorize)):
            if name not in grants.credential_names:
                raise HTTPException(403)
            try:
                return await LocalVersionedCredentialBinding(
                    self.secrets_store, name
                ).load()
            except CredentialNeedsReauthentication as exc:
                raise HTTPException(409, "Credential login is required") from exc

        @self.app.put("/credential/{name}")
        async def replace_credential(
            name: str, body: CredentialReplacement, grants=Depends(authorize)
        ):
            if (
                name not in grants.credential_names
                or not supports_file_credential_binding(name)
            ):
                raise HTTPException(403)
            try:
                version = await LocalVersionedCredentialBinding(
                    self.secrets_store, name
                ).replace(body.expected_version, body.value)
            except CredentialConflict as exc:
                raise HTTPException(409, "Credential changed") from exc
            return {"version": version}

    async def start(self) -> None:
        directory = self.socket_dir
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        path = directory / "credentials.sock"
        path.unlink(missing_ok=True)
        self._server = _SocketServer(
            uvicorn.Config(
                self.app,
                uds=str(path),
                lifespan="off",
                log_level="error",
                access_log=False,
            )
        )
        self._task = asyncio.create_task(self._server.serve())
        try:
            async with asyncio.timeout(10):
                while not self._server.started:
                    if self._task.done():
                        await self._task
                        raise RuntimeError("Credential broker failed to start")
                    await asyncio.sleep(0.01)
            path.chmod(0o600)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        try:
            if self._server is not None:
                self._server.should_exit = True
            if self._task is not None:
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(self._task, timeout=5)
        finally:
            (self.socket_dir / "credentials.sock").unlink(missing_ok=True)
            if self.socket_dir.exists():
                self.socket_dir.rmdir()
