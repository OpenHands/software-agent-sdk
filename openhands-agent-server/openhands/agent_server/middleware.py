"""CORS middleware for the agent server.

``CORSDispatcher`` routes requests to one of two CORS configurations
based on path:

* Workspace cookie endpoints (``/api/auth/workspace-session`` and
  ``/api/conversations/{id}/workspace/*``) — wildcard CORS that echoes
  the request Origin on every response. These are the only routes that
  authenticate via an ambient (cookie) credential.
* Everything else — ``LocalhostCORSMiddleware``, which honors the
  operator's ``allow_cors_origins`` and always allows localhost and
  ``DOCKER_HOST_ADDR`` (matches OpenHands/OpenHands#4624 intent).
"""

import os
import re
from urllib.parse import urlparse

from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


_WORKSPACE_SESSION_PATH = "/api/auth/workspace-session"
_WORKSPACE_STATIC_RE = re.compile(r"^/api/conversations/[^/]+/workspace(/|$)")


def _is_workspace_cookie_path(path: str) -> bool:
    if path == _WORKSPACE_SESSION_PATH:
        return True
    return bool(_WORKSPACE_STATIC_RE.match(path))


class LocalhostCORSMiddleware(CORSMiddleware):
    """``CORSMiddleware`` that always allows localhost and ``DOCKER_HOST_ADDR``."""

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        super().__init__(
            app,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def is_allowed_origin(self, origin: str) -> bool:
        if origin:
            hostname = urlparse(origin).hostname or ""
            if hostname in ("localhost", "127.0.0.1"):
                return True
            docker_host_addr = os.environ.get("DOCKER_HOST_ADDR")
            if docker_host_addr and hostname == docker_host_addr:
                return True
        return bool(super().is_allowed_origin(origin))


class CORSDispatcher:
    """Dispatches each request to the workspace or default CORS middleware.

    The workspace branch uses ``allow_origin_regex=r"https?://.+"`` rather
    than ``allow_origins=["*"]`` for two reasons:

    1. Starlette emits a literal ``*`` on simple responses when
       ``allow_all_origins`` is set and the request has no ``Cookie``
       header — which browsers reject together with
       ``Access-Control-Allow-Credentials: true``. The regex path always
       echoes the request Origin (with ``Vary: Origin``).
    2. Anchoring to ``http(s)://`` excludes ``Origin: null`` (sandboxed
       iframes, ``data:`` / ``blob:`` URLs), which have no defined CHIPS
       partition key and are not legitimate clients.
    """

    def __init__(self, app: ASGIApp, *, allow_origins: list[str]) -> None:
        self._default_cors = LocalhostCORSMiddleware(
            app, allow_origins=list(allow_origins)
        )
        self._workspace_cors = CORSMiddleware(
            app,
            allow_origin_regex=r"https?://.+",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            # Strip FastAPI ``root_path`` so dispatch works behind
            # reverse proxies mounted under a sub-path.
            root_path = scope.get("root_path", "")
            path = scope.get("path", "/")
            route_path = path.removeprefix(root_path) if root_path else path
            if _is_workspace_cookie_path(route_path or "/"):
                await self._workspace_cors(scope, receive, send)
                return
        await self._default_cors(scope, receive, send)
