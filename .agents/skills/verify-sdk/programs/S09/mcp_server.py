"""A small local MCP server for the S08/S09 recipes (FastMCP).

Usage: mcp_server.py [stdio | http PORT | sse PORT] [--name NAME]
                     [--slow-start SECONDS] [--fail-start]
                     [--oauth [--no-metadata]] [--fail-first-call]

--fail-first-call (http only) answers the first tools/call with HTTP 503,
a transient gateway error.

Tools: echo, add, getenv, counter (per-process state), nested (a nested
object schema), kind_echo (a property literally named `kind`), fail
(isError), picture (image content), structured (JSON result), slow,
headers (the HTTP request headers it received), die (exits the process
right after answering), register_extra / remove_extra (change the tool list
and send notifications/tools/list_changed).
"""

import asyncio
import base64
import os
import sys
import threading
import time
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context, get_http_headers
from fastmcp.utilities.types import Image
from mcp import types as mcp_types
from pydantic import BaseModel, Field


args = sys.argv[1:]
name = "verify-server"
if "--name" in args:
    name = args[args.index("--name") + 1]
if "--fail-start" in args:
    sys.exit("refusing to start")
if "--slow-start" in args:
    time.sleep(float(args[args.index("--slow-start") + 1]))

mcp = FastMCP(name)
COUNT = {"n": 0}
# A 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class Limits(BaseModel):
    max: int = Field(description="Upper bound.")


class Config(BaseModel):
    title: str = Field(description="A title.")
    tags: list[str] = Field(description="Some tags.")
    limits: Limits


@mcp.tool()
def echo(text: Annotated[str, Field(description="Text to echo back.")]) -> str:
    """Echo the text."""
    return f"echo: {text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def getenv(var: str, reverse: bool = False) -> str:
    """Return an environment variable of the server process (optionally
    reversed, so a secret value survives the client's masking)."""
    value = os.environ.get(var, "<unset>")
    return value[::-1] if reverse else value


@mcp.tool()
def reverse(text: str) -> str:
    """Return the text reversed."""
    return text[::-1]


@mcp.tool()
def counter() -> str:
    """Count calls in this server process."""
    COUNT["n"] += 1
    return f"count={COUNT['n']} pid={os.getpid()}"


@mcp.tool()
def nested(config: Config, note: str | None = None) -> str:
    """Accept a nested object."""
    return (
        f"title={config.title} tags={','.join(config.tags)} "
        f"max={config.limits.max} note={note}"
    )


@mcp.tool()
def kind_echo(kind: Annotated[str, Field(description="A field named kind.")]) -> str:
    """Echo a property literally named kind."""
    return f"kind={kind}"


@mcp.tool()
def fail(reason: str = "boom") -> str:
    """Always fails."""
    raise ValueError(f"tool failed: {reason}")


@mcp.tool(output_schema=None)
def picture() -> list:
    """Return text and an image."""
    return ["a red dot", Image(data=PNG, format="png")]


@mcp.tool()
def structured() -> dict:
    """Return structured JSON."""
    return {"answer": 42, "items": ["a", "b"]}


@mcp.tool()
def slow(seconds: float) -> str:
    """Sleep, then answer."""
    time.sleep(seconds)
    return "slept"


@mcp.tool()
def headers() -> dict:
    """The HTTP headers of this request."""
    return {k: v for k, v in get_http_headers(include_all=True).items()}


@mcp.tool()
def die() -> str:
    """Answer, then exit the server process."""
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return f"dying pid={os.getpid()}"


async def _notify() -> None:
    loop = asyncio.get_running_loop()
    loop.create_task(
        get_context().send_notification(mcp_types.ToolListChangedNotification())
    )


@mcp.tool()
async def register_extra() -> str:
    """Add a tool named extra and notify the client."""

    @mcp.tool()
    def extra(value: int) -> int:
        """Added after connect: doubles the value."""
        return value * 2

    await _notify()
    return "registered"


@mcp.tool()
async def remove_extra() -> str:
    """Remove the extra tool and notify the client."""
    mcp.local_provider.remove_tool("extra")
    await _notify()
    return "removed"


# OAuth endpoints for --oauth: authorization-server metadata (unless
# --no-metadata) and two token endpoints that record which one was hit.
if "--oauth" in args:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    oauth_log = os.environ.get("VERIFY_OAUTH_LOG", "oauth.log")

    def _log(line: str) -> None:
        with open(oauth_log, "a") as fh:
            fh.write(line + "\n")

    async def _token(request: Request) -> JSONResponse:
        form = await request.form()
        _log(
            f"{request.url.path} grant={form.get('grant_type')} "
            f"refresh={form.get('refresh_token')}"
        )
        return JSONResponse(
            {
                "access_token": "fresh-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "fresh-refresh-token",
            }
        )

    mcp.custom_route("/oauth/token", methods=["POST"])(_token)
    mcp.custom_route("/token", methods=["POST"])(_token)

    if "--no-metadata" not in args:

        @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
        async def _metadata(request: Request) -> JSONResponse:
            _log("metadata")
            origin = f"{request.url.scheme}://{request.url.netloc}"
            return JSONResponse(
                {
                    "issuer": origin,
                    "authorization_endpoint": f"{origin}/oauth/authorize",
                    "token_endpoint": f"{origin}/oauth/token",
                    "response_types_supported": ["code"],
                }
            )


if __name__ == "__main__":
    transport = args[0] if args and not args[0].startswith("--") else "stdio"
    if transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    elif "--fail-first-call" in args:
        import uvicorn
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import PlainTextResponse

        class FailFirstToolCall(BaseHTTPMiddleware):
            failed = False

            async def dispatch(self, request, call_next):
                body = (await request.body()).replace(b" ", b"")
                if not FailFirstToolCall.failed and b'"method":"tools/call"' in body:
                    FailFirstToolCall.failed = True
                    return PlainTextResponse("service unavailable", status_code=503)
                return await call_next(request)

        app = mcp.http_app(path="/mcp")
        app.add_middleware(FailFirstToolCall)
        uvicorn.run(app, host="127.0.0.1", port=int(args[1]), log_level="error")
    else:
        port = int(args[1])
        path = "/sse" if transport == "sse" else "/mcp"
        mcp.run(
            transport=transport,
            host="127.0.0.1",
            port=port,
            show_banner=False,
            path=path,
        )
