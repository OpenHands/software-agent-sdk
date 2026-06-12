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
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

import mcp.types
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

from openhands.agent_server._secrets_exposure import get_cipher
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.exceptions import MCPError, MCPTimeoutError
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import decrypt_str_with_cipher_or_keep


logger = get_logger(__name__)

mcp_router = APIRouter(prefix="/mcp", tags=["MCP"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
#
# We accept a single server spec instead of the full ``MCPConfig`` map. The
# UI flow this powers ("add a new MCP server") always validates one server
# at a time, and keeping the request shape narrow avoids exposing tuple-of-
# transports semantics the caller doesn't need.

_DEFAULT_SERVER_NAME = "test-server"


class _StdioMCPServerSpec(BaseModel):
    """Stdio (subprocess) MCP server spec.

    Mirrors the subset of ``fastmcp.mcp_config.StdioMCPServer`` fields the
    OpenHands UI exposes today.
    """

    type: Literal["stdio"] = "stdio"
    command: str = Field(..., min_length=1, description="Executable to invoke")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class _RemoteMCPServerSpec(BaseModel):
    """Remote (HTTP / SSE) MCP server spec."""

    # ``shttp`` is the alias the OpenHands settings layer uses for
    # streamable-http; we accept both spellings so the UI can forward
    # its own value unchanged.
    type: Literal["http", "shttp", "streamable-http", "sse"]
    url: str = Field(..., min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    api_key: str | None = Field(
        default=None,
        description=(
            "Bearer token. If provided, sent as 'Authorization: Bearer <token>'."
        ),
    )

    def to_fastmcp_dict(self) -> dict[str, Any]:
        # fastmcp's RemoteMCPServer accepts "http", "streamable-http", "sse";
        # collapse the OpenHands-specific "shttp" alias to "http".
        transport = "http" if self.type == "shttp" else self.type
        out: dict[str, Any] = {"url": self.url, "transport": transport}
        headers = dict(self.headers)
        if self.api_key:
            # Don't clobber a caller-provided Authorization header.
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        if headers:
            out["headers"] = headers
        return out


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
            "Name to use for the server inside the temporary MCPConfig. "
            "Only affects error messages -- does not need to match any "
            "persisted setting."
        ),
    )
    server: Annotated[
        _StdioMCPServerSpec | _RemoteMCPServerSpec,
        Field(discriminator="type"),
    ]
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
        # Mirror the validation MCPConfig itself applies to server keys --
        # whitespace-only names would silently bypass min_length=1 above.
        self.name = self.name.strip() or _DEFAULT_SERVER_NAME
        return self


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


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def _decrypt_mapping(cipher: Cipher | None, mapping: dict[str, str]) -> dict[str, str]:
    """Decrypt Fernet-encrypted values round-tripped from settings.

    The GUI fetches stored settings with ``X-Expose-Secrets: encrypted`` and
    forwards the ciphertext unchanged so the edit flow can test the *real*
    stored credentials without ever seeing them. Plaintext values (the
    common case: freshly typed input) pass through untouched.
    """
    if cipher is None:
        return dict(mapping)
    return {
        key: decrypt_str_with_cipher_or_keep(
            cipher, value, description="MCP test env/headers"
        )
        for key, value in mapping.items()
    }


def _server_to_fastmcp_dict(
    spec: _StdioMCPServerSpec | _RemoteMCPServerSpec, cipher: Cipher | None
) -> dict:
    if isinstance(spec, _StdioMCPServerSpec):
        out: dict[str, Any] = {"command": spec.command, "args": list(spec.args)}
        if spec.env:
            out["env"] = _decrypt_mapping(cipher, spec.env)
        if spec.cwd:
            out["cwd"] = spec.cwd
        return out
    remote = spec.to_fastmcp_dict()
    if "headers" in remote:
        remote["headers"] = _decrypt_mapping(cipher, remote["headers"])
    return remote


def _run_tool_call(
    client: Any, spec: MCPToolCallSpec, tool_names: list[str], timeout: float
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
    request: MCPTestRequest, cipher: Cipher | None
) -> MCPTestResponse:
    """Synchronous probe -- safe to run inside ``run_in_executor``.

    ``create_mcp_tools`` already runs its own event loop in a background
    thread via ``MCPClient.call_async_from_sync``. We deliberately do not
    call it from the FastAPI request task; instead the caller hops into a
    threadpool first.
    """

    config = {
        "mcpServers": {request.name: _server_to_fastmcp_dict(request.server, cipher)}
    }

    try:
        # ``create_mcp_tools`` returns a client that owns a background loop
        # and a (possibly long-lived) subprocess. Use the context-manager
        # form so we always tear it down, even when listing succeeded.
        with create_mcp_tools(config, timeout=request.timeout) as client:
            tool_names = [tool.name for tool in client.tools]
            tool_result: MCPToolCallResult | None = None
            if request.tool_call is not None:
                tool_result = _run_tool_call(
                    client, request.tool_call, tool_names, request.timeout
                )
            return MCPTestSuccess(tools=tool_names, tool_result=tool_result)
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
    summary="Test an MCP server configuration",
    description=(
        "Attempt to connect to a candidate MCP server and list its tools, "
        "without persisting any settings. Useful for validating user input "
        "in 'add MCP server' flows before storing the config. "
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
