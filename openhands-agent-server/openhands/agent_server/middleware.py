"""CORS middleware for the agent server.

The agent server has two distinct CORS requirements:

1. **Most endpoints** authenticate via the ``X-Session-API-Key`` header.
   Browsers never auto-attach custom headers to cross-origin requests, so
   CORS on these routes is not a security boundary — it only controls
   which other-origin SPAs are *allowed* to call the API from
   ``fetch()``. Operators configure this via ``OH_ALLOW_CORS_ORIGINS``;
   localhost/loopback and ``DOCKER_HOST_ADDR`` are always allowed for
   developer ergonomics (the original purpose of ``LocalhostCORSMiddleware``
   — see OpenHands/OpenHands#4624).

2. **The workspace cookie endpoints** are the one place where CORS is a
   real security boundary, because they handle an ambient credential
   (the ``oh_workspace_session_key`` cookie, ``SameSite=None; Secure;
   Partitioned``). These routes are:

     * ``POST`` / ``DELETE`` ``/api/auth/workspace-session`` — mint/clear
       the cookie
     * ``GET`` ``/api/conversations/{id}/workspace/...`` — workspace
       static files served using the cookie

   These routes **always accept CORS from any origin** with credentials,
   because the actual security boundary is enforced elsewhere:

     * Minting still requires ``X-Session-API-Key``, so an arbitrary
       origin cannot mint a cookie it doesn't already have the key for.
     * The cookie is ``Partitioned`` (CHIPS), scoping it to the embedding
       top-level site that minted it — a different top-level site cannot
       piggy-back on someone else's workspace cookie.

   Because ``allow_credentials=True``, Starlette echoes the request
   ``Origin`` back rather than emitting a literal ``*`` (browsers reject
   ``*`` with credentials), so credentialed fetches from any origin
   actually work.

The single global ``CORSDispatcher`` middleware routes each request to
the appropriate underlying ``LocalhostCORSMiddleware`` based on the
request path, after stripping any ``root_path`` set via FastAPI (so the
dispatch is correct behind reverse proxies that mount this server under
a sub-path).
"""

import os
import re
from urllib.parse import urlparse

from fastapi.middleware.cors import CORSMiddleware
from starlette._utils import get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send


# Route paths (post-``root_path`` stripping) that use cookie auth and
# therefore always accept CORS from any origin.
_WORKSPACE_SESSION_PATH = "/api/auth/workspace-session"
_WORKSPACE_STATIC_RE = re.compile(r"^/api/conversations/[^/]+/workspace(/|$)")


def _is_workspace_cookie_path(path: str) -> bool:
    if path == _WORKSPACE_SESSION_PATH:
        return True
    return bool(_WORKSPACE_STATIC_RE.match(path))


class LocalhostCORSMiddleware(CORSMiddleware):
    """``CORSMiddleware`` that always allows localhost and ``DOCKER_HOST_ADDR``.

    The auto-allow is unconditional — it applies regardless of what's in
    ``allow_origins`` — matching the original intent from
    OpenHands/OpenHands#4624 ("any localhost/127.0.0.1 request,
    regardless of port") and the documented behavior on
    ``Config.allow_cors_origins``.

    For every other origin, this delegates to the parent
    ``CORSMiddleware`` and its configured ``allow_origins`` list.
    """

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
            parsed = urlparse(origin)
            hostname = parsed.hostname or ""

            # Always allow localhost/127.0.0.1 regardless of port — this
            # is the whole reason LocalhostCORSMiddleware exists.
            if hostname in ("localhost", "127.0.0.1"):
                return True

            # Also always allow DOCKER_HOST_ADDR if set (remote browser
            # access against agent-server containers).
            docker_host_addr = os.environ.get("DOCKER_HOST_ADDR")
            if docker_host_addr and hostname == docker_host_addr:
                return True

        # For any other origin (or a missing Origin header), fall back
        # to the configured allowlist.
        result: bool = super().is_allowed_origin(origin)
        return result


class CORSDispatcher:
    """Routes each request to the correct CORS middleware by path.

    * Workspace cookie endpoints (see module docstring) → wildcard CORS.
    * Everything else → ``LocalhostCORSMiddleware`` configured with the
      operator-supplied ``allow_origins`` list.

    The path lookup uses ``starlette._utils.get_route_path`` so that
    deployments behind a reverse proxy that mounts this server under a
    sub-path (FastAPI's ``root_path`` / ``OH_WEB_URL``) still match
    workspace routes correctly.

    Each wrapped ``LocalhostCORSMiddleware`` is constructed once at
    startup so that Starlette's precomputed preflight/simple headers
    are reused across requests; there is no per-request middleware
    instantiation cost.
    """

    def __init__(self, app: ASGIApp, *, allow_origins: list[str]) -> None:
        self._default_cors = LocalhostCORSMiddleware(
            app, allow_origins=list(allow_origins)
        )
        # Wildcard. With ``allow_credentials=True``, Starlette echoes the
        # request Origin back rather than emitting a literal "*", which
        # is what browsers require for credentialed CORS.
        self._workspace_cors = LocalhostCORSMiddleware(app, allow_origins=["*"])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and _is_workspace_cookie_path(
            get_route_path(scope)
        ):
            await self._workspace_cors(scope, receive, send)
            return
        await self._default_cors(scope, receive, send)
