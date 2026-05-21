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

   These routes accept CORS from any origin with credentials. The actual
   security boundary is enforced elsewhere:

     * Minting still requires ``X-Session-API-Key``, so an arbitrary
       origin cannot mint a cookie it doesn't already have the key for.
     * The cookie is ``Partitioned`` (CHIPS), scoping it to the embedding
       top-level site that minted it — at least on browsers that
       implement CHIPS (Chromium/Edge today; Firefox/Safari coverage
       still in progress).

   Wildcard credentialed CORS uses ``allow_origin_regex=r".*"`` instead
   of ``allow_origins=["*"]``. Starlette's ``CORSMiddleware`` treats the
   literal ``"*"`` as ``allow_all_origins=True`` and then emits the
   string ``"*"`` on actual (non-preflight) responses unless the request
   already carries a ``Cookie`` header — which fails the very first mint
   request that creates the cookie, because browsers reject
   ``Access-Control-Allow-Origin: *`` together with
   ``Access-Control-Allow-Credentials: true``. The regex path
   unconditionally echoes the request ``Origin`` back, which is what
   credentialed CORS actually requires.

The single global ``CORSDispatcher`` middleware routes each request to
the appropriate underlying middleware based on the request path, after
stripping any ``root_path`` set via FastAPI (so the dispatch is correct
behind reverse proxies that mount this server under a sub-path).
"""

import os
import re
from urllib.parse import urlparse

from fastapi.middleware.cors import CORSMiddleware
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

    * Workspace cookie endpoints (see module docstring) → wildcard CORS
      that echoes the request Origin on every response (preflight and
      actual). Implemented via ``allow_origin_regex=r".*"``, not
      ``allow_origins=["*"]``, because Starlette's literal-``"*"``
      handling on simple responses breaks credentialed CORS on requests
      that don't carry a ``Cookie`` header — including the first mint
      request that creates the cookie.
    * Everything else → ``LocalhostCORSMiddleware`` configured with the
      operator-supplied ``allow_origins`` list.

    The path lookup strips ``scope['root_path']`` from ``scope['path']``
    the same way Starlette's router does, so deployments behind a
    reverse proxy that mount this server under a sub-path (FastAPI's
    ``root_path`` / ``OH_WEB_URL``) still match workspace routes
    correctly. We inline the trivial strip rather than call Starlette's
    private ``_utils.get_route_path``, which has no stability guarantees.

    Each wrapped middleware is constructed once at startup so that
    Starlette's precomputed preflight/simple headers are reused across
    requests; there is no per-request middleware instantiation cost.
    """

    def __init__(self, app: ASGIApp, *, allow_origins: list[str]) -> None:
        self._default_cors = LocalhostCORSMiddleware(
            app, allow_origins=list(allow_origins)
        )
        # Match any origin via regex. With allow_credentials=True this
        # causes Starlette to echo the request Origin on both preflight
        # and actual responses (with a ``Vary: Origin`` so caches don't
        # collapse responses across origins).
        self._workspace_cors = CORSMiddleware(
            app,
            allow_origin_regex=r".*",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            root_path = scope.get("root_path", "")
            path = scope.get("path", "/")
            # Strip the proxy/root prefix the same way Starlette's
            # router does before matching.
            route_path = path.removeprefix(root_path) if root_path else path
            if not route_path:
                route_path = "/"
            if _is_workspace_cookie_path(route_path):
                await self._workspace_cors(scope, receive, send)
                return
        await self._default_cors(scope, receive, send)
