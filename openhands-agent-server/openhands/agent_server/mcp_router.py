"""MCP router for OpenHands SDK.

Exposes a single endpoint, ``POST /api/mcp/test``, that lets clients verify
a candidate MCP server configuration in isolation -- before persisting it
to settings, where a misconfiguration would otherwise surface only at
conversation start (and there manifest as a noisy traceback that aborts
agent initialization).

The endpoint never mutates server state or touches stored settings: it
spins up the MCP connection, lists the advertised tools, optionally invokes
one caller-chosen tool (``tool_call``), then tears the connection down.
The optional tool call exists because listing tools does not exercise the
credentials many servers only use inside tool handlers (e.g. the Slack MCP
server starts fine with a bogus token); callers must pick a read-only tool.
For OAuth MCP servers, any token/client metadata acquired during the probe is
returned on the success response's ``oauth_state`` field so the caller can
persist it through the settings API under the tested server's ``auth.state``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

import anyio
import httpx
import mcp.types
from fastapi import APIRouter, HTTPException, Request
from fastmcp.client.auth.oauth import ClientNotFoundError, OAuth
from fastmcp.client.oauth_callback import (
    OAuthCallbackResult,
    create_oauth_callback_server,
)
from pydantic import BaseModel, Field, model_validator

from openhands.agent_server._secrets_exposure import get_cipher
from openhands.agent_server.mcp_oauth_store import (
    InMemoryMCPOAuthTokenStore,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import (
    MCPOAuthAuthCredential,
    MCPOAuthAuthentication,
    MCPOAuthStateResponse,
    MCPServer,
)
from openhands.sdk.mcp.exceptions import MCPError, MCPTimeoutError
from openhands.sdk.utils.cipher import Cipher


logger = get_logger(__name__)

mcp_router = APIRouter(prefix="/mcp", tags=["MCP"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
#
# We accept one canonical MCPServer instead of the full MCP server map. The UI
# flow this powers ("add a new MCP server") validates one server at a time; the
# route wraps it in a temporary map only at the runtime boundary.

_DEFAULT_SERVER_NAME = "test-server"


class MCPToolCallSpec(BaseModel):
    """A single tool invocation to run as part of the connection test.

    Listing tools does not exercise the credentials many servers only use
    inside tool handlers, so callers can name one tool to invoke after the
    listing succeeds. Callers are responsible for choosing a read-only tool;
    the endpoint executes it verbatim.
    """

    name: str = Field(..., min_length=1, description="Name of the tool to invoke")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments passed to the tool unchanged.",
    )


class MCPTestRequest(BaseModel):
    """Body for ``POST /api/mcp/test``."""

    name: str = Field(
        default=_DEFAULT_SERVER_NAME,
        min_length=1,
        max_length=128,
        description=(
            "Name to use for the server inside the temporary MCP server map. "
            "Only affects error messages -- does not need to match any "
            "persisted setting."
        ),
    )
    server: MCPServer
    timeout: float = Field(
        default=15.0,
        gt=0,
        le=120,
        description="Seconds to wait for connection + tools/list to complete.",
    )
    tool_call: MCPToolCallSpec | None = Field(
        default=None,
        description=(
            "Optional read-only tool to invoke after listing succeeds, so "
            "callers can verify credentials the server only exercises on "
            "tool invocation. Its outcome is reported verbatim in "
            "`tool_result` without affecting `ok`."
        ),
    )

    @model_validator(mode="after")
    def _strip_name(self) -> MCPTestRequest:
        # Mirror the validation the MCP server map itself applies to server keys --
        # whitespace-only names would silently bypass min_length=1 above.
        self.name = self.name.strip() or _DEFAULT_SERVER_NAME
        return self

    def to_mcp_config(self, *, cipher: Cipher | None = None) -> dict[str, MCPServer]:
        return {self.name: self.server.with_decrypted_secrets(cipher=cipher)}


class MCPToolCallResult(BaseModel):
    """Verbatim outcome of the requested ``tool_call``.

    The endpoint stays provider-neutral: many servers report upstream
    failures (e.g. Slack's ``{"ok": false, "error": "invalid_auth"}``)
    as ordinary text content with ``isError`` unset, so interpreting the
    payload is the caller's job.
    """

    is_error: bool = Field(description="The MCP-level isError flag of the result.")
    text: str = Field(description="Concatenated text content of the result.")


class MCPTestSuccess(BaseModel):
    """Response when the candidate server connects and lists its tools."""

    ok: Literal[True] = True
    tools: list[str] = Field(
        default_factory=list,
        description="Names of tools advertised by the MCP server.",
    )
    tool_result: MCPToolCallResult | None = Field(
        default=None,
        description=("Outcome of the requested `tool_call`, when one was supplied."),
    )
    oauth_state: MCPOAuthStateResponse | None = Field(
        default=None,
        description=(
            "Serialized OAuth state acquired or refreshed by the probe. "
            "Clients should persist this under the tested server's auth.state."
        ),
    )


class MCPTestFailure(BaseModel):
    """Response when the candidate server fails to connect or list tools.

    The endpoint returns HTTP 200 in both success and failure cases: a
    failure here is the *expected* outcome of validating a user-supplied
    config, not a server-side error. The structured shape makes it easy
    for the UI to render an actionable message.
    """

    ok: Literal[False] = False
    error: str = Field(description="Human-readable error message.")
    error_kind: Literal["timeout", "connection", "unknown"] = Field(
        description="Coarse error classification, useful for branching UI."
    )


MCPTestResponse = MCPTestSuccess | MCPTestFailure


class MCPOAuthStartResponse(BaseModel):
    """Response for starting an install-time OAuth MCP probe."""

    ok: bool
    job_id: str | None = None
    authorization_url: str | None = None
    error: str | None = None
    error_kind: Literal["timeout", "connection", "unknown"] | None = None


class MCPOAuthStatusResponse(BaseModel):
    """Current state of an install-time OAuth MCP probe."""

    ok: bool
    status: Literal["pending", "authorizing", "succeeded", "failed"]
    job_id: str
    authorization_url: str | None = None
    callback_ready: bool = False
    tools: list[str] | None = None
    tool_result: MCPToolCallResult | None = None
    oauth_state: MCPOAuthStateResponse | None = None
    error: str | None = None
    error_kind: Literal["timeout", "connection", "unknown"] | None = None


class MCPOAuthCallbackRequest(BaseModel):
    """Callback URL copied from a browser OAuth redirect."""

    callback_url: str = Field(..., min_length=1)


class _MCPOAuthProbeJob:
    def __init__(self, *, request: MCPTestRequest, cipher: Cipher | None):
        self.id = uuid.uuid4().hex
        self.request = request
        self.cipher = cipher
        self.authorization_url: str | None = None
        self.callback_url: str | None = None
        self.result: MCPTestResponse | None = None
        self.status: Literal["pending", "authorizing", "succeeded", "failed"] = (
            "pending"
        )
        self.authorization_ready = threading.Event()
        self.callback_ready = threading.Event()
        self.done = threading.Event()
        self.lock = threading.Lock()

    def set_authorization_url(self, authorization_url: str) -> None:
        with self.lock:
            self.authorization_url = authorization_url
            self.status = "authorizing"
        self.authorization_ready.set()

    def set_callback_ready(self, callback_url: str) -> None:
        with self.lock:
            self.callback_url = callback_url
        self.callback_ready.set()

    def set_result(self, result: MCPTestResponse) -> None:
        with self.lock:
            self.result = result
            self.status = (
                "succeeded" if isinstance(result, MCPTestSuccess) else "failed"
            )
        self.done.set()

    def to_status_response(self) -> MCPOAuthStatusResponse:
        with self.lock:
            result = self.result
            status = self.status
            authorization_url = self.authorization_url

        if isinstance(result, MCPTestSuccess):
            return MCPOAuthStatusResponse(
                ok=True,
                status="succeeded",
                job_id=self.id,
                authorization_url=authorization_url,
                callback_ready=self.callback_ready.is_set(),
                tools=result.tools,
                tool_result=result.tool_result,
                oauth_state=result.oauth_state,
            )
        if isinstance(result, MCPTestFailure):
            return MCPOAuthStatusResponse(
                ok=False,
                status="failed",
                job_id=self.id,
                authorization_url=authorization_url,
                callback_ready=self.callback_ready.is_set(),
                error=result.error,
                error_kind=result.error_kind,
            )
        return MCPOAuthStatusResponse(
            ok=True,
            status=status,
            job_id=self.id,
            authorization_url=authorization_url,
            callback_ready=self.callback_ready.is_set(),
        )


_oauth_probe_jobs: dict[str, _MCPOAuthProbeJob] = {}
_oauth_probe_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def _oauth_auth_from_authentication(
    authentication: MCPOAuthAuthentication | None,
    *,
    oauth_token_storage: InMemoryMCPOAuthTokenStore | None,
    job: _MCPOAuthProbeJob,
) -> OAuth:
    additional_client_metadata: dict[str, Any] = {}
    if authentication is not None:
        additional_client_metadata.update(
            authentication.additional_client_metadata or {}
        )
        if authentication.client_auth_method is not None:
            additional_client_metadata["token_endpoint_auth_method"] = (
                authentication.client_auth_method
            )
    return _BrowserCoordinatedOAuth(
        job=job,
        scopes=authentication.scopes if authentication is not None else None,
        client_name=(
            authentication.client_name
            if authentication is not None and authentication.client_name
            else "FastMCP Client"
        ),
        token_storage=oauth_token_storage,
        additional_client_metadata=additional_client_metadata or None,
        client_metadata_url=(
            authentication.client_metadata_url if authentication is not None else None
        ),
        client_id=authentication.client_id if authentication is not None else None,
        client_secret=(
            authentication.client_secret.get_secret_value()
            if authentication is not None and authentication.client_secret is not None
            else None
        ),
    )


class _BrowserCoordinatedOAuth(OAuth):
    """FastMCP OAuth client that lets the frontend own browser navigation."""

    def __init__(self, *, job: _MCPOAuthProbeJob, **kwargs: Any):
        super().__init__(**kwargs)
        self._job = job

    async def redirect_handler(self, authorization_url: str) -> None:
        """Capture the URL instead of opening a browser from the backend."""
        async with self.httpx_client_factory() as client:
            response = await client.get(authorization_url, follow_redirects=False)
            if response.status_code == 400:
                raise ClientNotFoundError(
                    "OAuth client not found - cached credentials may be stale"
                )
            if response.status_code not in (200, 302, 303, 307, 308):
                raise RuntimeError(
                    f"Unexpected authorization response: {response.status_code}"
                )

        logger.info("MCP OAuth authorization URL captured for job %s", self._job.id)
        self._job.set_authorization_url(authorization_url)

    async def callback_handler(self) -> tuple[str, str | None]:
        """Run FastMCP's callback server and expose readiness to the frontend."""
        result = OAuthCallbackResult()
        result_ready = anyio.Event()
        server = create_oauth_callback_server(
            port=self.redirect_port,
            server_url=self.mcp_url,
            result_container=result,
            result_ready=result_ready,
        )
        callback_url = f"http://localhost:{self.redirect_port}/callback"
        callback_timeout = 300.0

        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            self._job.set_callback_ready(callback_url)
            logger.info(
                "MCP OAuth callback server ready for job %s at %s",
                self._job.id,
                callback_url,
            )

            try:
                with anyio.fail_after(callback_timeout):
                    await result_ready.wait()
                    if result.error:
                        raise result.error
                    return result.code, result.state  # type: ignore[return-value]
            except TimeoutError as e:
                raise TimeoutError(
                    f"OAuth callback timed out after {callback_timeout} seconds"
                ) from e
            finally:
                server.should_exit = True
                await anyio.sleep(0.1)
                tg.cancel_scope.cancel()

        raise RuntimeError("OAuth callback handler could not be started")


def _run_tool_call(
    client: MCPClient, spec: MCPToolCallSpec, tool_names: list[str], timeout: float
) -> MCPToolCallResult:
    """Invoke the requested tool on the connected client.

    Uses ``call_tool_mcp`` (not ``call_tool``, which raises on ``isError``)
    so in-band failures come back as data -- mirrors ``MCPToolExecutor``.
    A timeout is reported as an errored result rather than failing the
    whole test: the server did connect and list, which is still useful.
    """
    if spec.name not in tool_names:
        return MCPToolCallResult(
            is_error=True,
            text=(
                f"Tool {spec.name!r} not advertised by server "
                f"(available: {', '.join(tool_names) or 'none'})"
            ),
        )
    try:
        result: mcp.types.CallToolResult = client.call_async_from_sync(
            client.call_tool_mcp,
            name=spec.name,
            arguments=spec.arguments,
            timeout=timeout,
        )
    except TimeoutError:
        return MCPToolCallResult(
            is_error=True,
            text=f"Tool {spec.name!r} call timed out after {timeout} seconds",
        )
    text = "\n".join(
        block.text
        for block in result.content
        if isinstance(block, mcp.types.TextContent)
    )
    return MCPToolCallResult(is_error=bool(result.isError), text=text)


def _probe_mcp_server(
    request: MCPTestRequest,
    cipher: Cipher | None,
    mcp_oauth_factory: Any | None = None,
) -> MCPTestResponse:
    """Synchronous probe -- safe to run inside ``run_in_executor``.

    ``create_mcp_tools`` already runs its own event loop in a background
    thread via ``MCPClient.call_async_from_sync``. We deliberately do not
    call it from the FastAPI request task; instead the caller hops into a
    threadpool first.
    """

    mcp_config = request.to_mcp_config(cipher=cipher)

    try:
        server = request.server
        oauth_auth = server.oauth_auth
        oauth_token_storage: InMemoryMCPOAuthTokenStore | None = None
        if oauth_auth is not None:
            oauth_token_storage = InMemoryMCPOAuthTokenStore(
                state=server.initial_oauth_state(cipher=cipher)
            )
        # ``create_mcp_tools`` returns a client that owns a background loop
        # and a (possibly long-lived) subprocess. Use the context-manager
        # form so we always tear it down, even when listing succeeded.
        create_tools_kwargs: dict[str, Any] = {
            "mcp_oauth_token_storage": oauth_token_storage
        }
        if mcp_oauth_factory is not None:
            create_tools_kwargs["mcp_oauth_factory"] = mcp_oauth_factory
        with create_mcp_tools(
            mcp_config,
            timeout=request.timeout,
            **create_tools_kwargs,
        ) as client:
            tool_names = [tool.name for tool in client.tools]
            tool_result: MCPToolCallResult | None = None
            if request.tool_call is not None:
                tool_result = _run_tool_call(
                    client,
                    request.tool_call,
                    tool_names,
                    request.timeout,
                )
            oauth_state: MCPOAuthStateResponse | None = None
            if oauth_token_storage is not None:
                state = oauth_token_storage.export_state()
                if state.has_values:
                    oauth_state = state.to_response(cipher=cipher)
            return MCPTestSuccess(
                tools=tool_names,
                tool_result=tool_result,
                oauth_state=oauth_state,
            )
    except MCPTimeoutError as exc:
        logger.info("MCP test timed out for server %r: %s", request.name, exc)
        return MCPTestFailure(error=str(exc), error_kind="timeout")
    except MCPError as exc:
        # ``MCPError("MCP Connection Failure")`` is what client.connect()
        # raises when the underlying fastmcp client fails to start. Surface
        # the root-cause message (e.g. "sh: 1: mcp-server-github: Permission
        # denied") because the wrapper alone isn't useful.
        cause = exc.__cause__ or exc.__context__
        detail = str(cause) if cause else str(exc) or "Failed to connect to MCP server"
        logger.info(
            "MCP test connection failed for server %r: %s", request.name, detail
        )
        return MCPTestFailure(error=detail, error_kind="connection")
    except Exception as exc:  # noqa: BLE001 - we want to surface anything else
        # Any other exception is unexpected but should still return a
        # structured response: the UI can't recover from a 500.
        logger.warning(
            "MCP test failed unexpectedly for server %r",
            request.name,
            exc_info=True,
        )
        return MCPTestFailure(
            error=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
            error_kind="unknown",
        )


@mcp_router.post(
    "/test",
    response_model=MCPTestResponse,
    response_model_exclude_none=True,
    summary="Test an MCP server configuration",
    description=(
        "Attempt to connect to a candidate MCP server and list its tools, "
        "without persisting any settings. Useful for validating user input "
        "in 'add MCP server' flows before storing the config. "
        "For OAuth servers, any acquired state is returned as `oauth_state` "
        "so clients can persist it under the MCP server object's `auth.state`. "
        "Optionally invokes one caller-chosen (read-only) tool via "
        "`tool_call` and reports its outcome in `tool_result`, so callers "
        "can verify credentials that are only exercised on tool invocation. "
        "Encrypted `env`/`headers` values round-tripped from settings are "
        "decrypted before the connection is attempted. "
        "Returns 200 with `ok=false` for connection / timeout failures "
        "(those are expected during validation, not server errors)."
    ),
)
async def test_mcp_server(
    request: MCPTestRequest, http_request: Request
) -> MCPTestResponse:
    """Probe a single MCP server config and report whether it works."""
    # Resolve the cipher here: the threadpool function below must not
    # reach back into ``http_request.app.state``.
    cipher = get_cipher(http_request)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _probe_mcp_server, request, cipher)


def _run_oauth_probe_job(job: _MCPOAuthProbeJob) -> None:
    def oauth_factory(
        _server_name: str,
        _server: MCPServer,
        auth: MCPOAuthAuthCredential,
        oauth_token_storage: InMemoryMCPOAuthTokenStore | None,
    ) -> OAuth:
        return _oauth_auth_from_authentication(
            auth.authentication,
            oauth_token_storage=oauth_token_storage,
            job=job,
        )

    result = _probe_mcp_server(
        job.request,
        job.cipher,
        mcp_oauth_factory=oauth_factory,
    )
    job.set_result(result)


def _register_oauth_job(job: _MCPOAuthProbeJob) -> None:
    with _oauth_probe_jobs_lock:
        _oauth_probe_jobs[job.id] = job


def _get_oauth_job(job_id: str) -> _MCPOAuthProbeJob:
    with _oauth_probe_jobs_lock:
        job = _oauth_probe_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="MCP OAuth job not found")
    return job


@mcp_router.post(
    "/oauth/start",
    response_model=MCPOAuthStartResponse,
    response_model_exclude_none=True,
    summary="Start an MCP OAuth install probe",
)
async def start_mcp_oauth(
    request: MCPTestRequest, http_request: Request
) -> MCPOAuthStartResponse:
    """Start OAuth for a candidate MCP server and return the authorization URL."""
    if request.server.oauth_auth is None:
        raise HTTPException(
            status_code=400,
            detail="MCP OAuth start requires auth.strategy='oauth2'",
        )

    job = _MCPOAuthProbeJob(request=request, cipher=get_cipher(http_request))
    _register_oauth_job(job)
    thread = threading.Thread(
        target=_run_oauth_probe_job,
        args=(job,),
        name=f"mcp-oauth-{job.id[:8]}",
        daemon=True,
    )
    thread.start()

    authorization_timeout = min(max(request.timeout, 1.0), 30.0)
    loop = asyncio.get_running_loop()
    authorization_ready = await loop.run_in_executor(
        None,
        job.authorization_ready.wait,
        authorization_timeout,
    )
    if authorization_ready and job.authorization_url is not None:
        return MCPOAuthStartResponse(
            ok=True,
            job_id=job.id,
            authorization_url=job.authorization_url,
        )

    if job.done.is_set() and isinstance(job.result, MCPTestFailure):
        return MCPOAuthStartResponse(
            ok=False,
            job_id=job.id,
            error=job.result.error,
            error_kind=job.result.error_kind,
        )

    return MCPOAuthStartResponse(
        ok=False,
        job_id=job.id,
        error="Timed out waiting for OAuth authorization URL",
        error_kind="timeout",
    )


@mcp_router.get(
    "/oauth/status/{job_id}",
    response_model=MCPOAuthStatusResponse,
    response_model_exclude_none=True,
    summary="Get an MCP OAuth install probe status",
)
async def get_mcp_oauth_status(job_id: str) -> MCPOAuthStatusResponse:
    return _get_oauth_job(job_id).to_status_response()


def _validate_callback_url(callback_url: str, job: _MCPOAuthProbeJob) -> None:
    parsed = urlparse(callback_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise HTTPException(status_code=400, detail="Invalid OAuth callback URL")

    with job.lock:
        expected = job.callback_url
    if expected is None:
        raise HTTPException(status_code=409, detail="OAuth callback is not ready")

    expected_parsed = urlparse(expected)
    if parsed.port != expected_parsed.port or parsed.path != expected_parsed.path:
        raise HTTPException(status_code=400, detail="Unexpected OAuth callback URL")


@mcp_router.post(
    "/oauth/callback/{job_id}",
    response_model=MCPOAuthStatusResponse,
    response_model_exclude_none=True,
    summary="Submit an MCP OAuth callback URL",
)
async def submit_mcp_oauth_callback(
    job_id: str, request: MCPOAuthCallbackRequest
) -> MCPOAuthStatusResponse:
    job = _get_oauth_job(job_id)
    _validate_callback_url(request.callback_url, job)
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        response = await client.get(request.callback_url)
    if response.status_code >= 400:
        return MCPOAuthStatusResponse(
            ok=False,
            status="failed",
            job_id=job.id,
            authorization_url=job.authorization_url,
            callback_ready=job.callback_ready.is_set(),
            error=f"OAuth callback returned HTTP {response.status_code}",
            error_kind="connection",
        )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, job.done.wait, 5.0)
    return job.to_status_response()
