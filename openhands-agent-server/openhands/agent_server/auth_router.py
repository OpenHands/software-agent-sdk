"""Workspace static-server cookie auth endpoints.

Browsers cannot attach custom headers to ``<iframe src>``, ``<img src>`` or
top-level navigation requests, so the workspace static file server cannot
be authenticated by the ``X-Session-API-Key`` header alone when the canvas
frontend wants to embed workspace artifacts (HTML reports, plots, PDFs).

These endpoints let a client that already has a valid session API key
exchange it for a short-lived cookie which the browser will automatically
attach to every workspace request — including cross-site iframes, thanks
to ``SameSite=None; Secure; Partitioned``.

The cookie is honored by ``workspace_router`` ONLY. Every other API route
continues to require the ``X-Session-API-Key`` header. This is deliberate:
keeping cookies off the rest of the API removes the CSRF surface that
cookie auth would otherwise add.
"""

from fastapi import APIRouter, Request, Response, status

from openhands.agent_server.dependencies import WORKSPACE_SESSION_COOKIE_NAME


auth_router = APIRouter(prefix="/auth", tags=["Auth"])

# Cookie lifetime in seconds. Short enough that a stolen cookie isn't a
# long-lived credential; long enough that a user previewing artifacts in
# canvas isn't constantly re-authing. The cookie auto-renews on every call
# to POST /api/auth/workspace-session, which the canvas frontend can do on
# load.
_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 8  # 8 hours

# Path scope: only sent on workspace-router URLs. Other /api/* endpoints
# never see the cookie.
_COOKIE_PATH = "/api/conversations"

# Hostnames the browser treats as "secure contexts" even over plain HTTP, so
# we can issue ``Secure`` cookies against them in local development without
# requiring TLS. Matches the platform-secure-contexts list in the WHATWG
# Secure Contexts spec.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _request_is_secure_context(request: Request) -> bool:
    """Whether the request originated from a context where the browser
    will accept ``Secure`` cookies.

    That's true for:
      - HTTPS (honoring ``X-Forwarded-Proto`` set by trusted proxies that
        terminate TLS in front of us), and
      - Plain HTTP against loopback hostnames, which browsers (per the
        Secure Contexts spec) treat as secure.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    scheme = forwarded_proto.split(",")[0].strip() or request.url.scheme
    if scheme == "https":
        return True

    forwarded_host = request.headers.get("x-forwarded-host", "")
    host = forwarded_host.split(",")[0].strip() or request.url.hostname or ""
    # Strip an optional ``:port`` suffix; IPv6 hosts are bracketed.
    if host.startswith("["):
        host = host.partition("]")[0].lstrip("[")
    else:
        host = host.split(":")[0]
    return host.lower() in _LOOPBACK_HOSTS


def _set_workspace_cookie(
    response: Response, *, value: str, secure: bool, max_age: int
) -> None:
    """Issue the workspace session cookie.

    Cross-site iframe support requires ``SameSite=None; Secure``. Modern
    Chrome additionally requires ``Partitioned`` (CHIPS) for cookies set
    in third-party contexts; without it, the cookie may be silently
    dropped under third-party-cookie phase-out.

    We always set ``SameSite=None`` so the same cookie works for both
    same-site and cross-site iframes, and always set ``HttpOnly`` so JS
    in workspace HTML can't read it back. ``Secure`` is set whenever
    the request comes from a secure context (HTTPS or loopback) — the
    only contexts where a ``SameSite=None`` cookie will actually be
    stored by the browser.
    """
    response.set_cookie(
        key=WORKSPACE_SESSION_COOKIE_NAME,
        value=value,
        max_age=max_age,
        path=_COOKIE_PATH,
        secure=secure,
        httponly=True,
        samesite="none",
    )
    # Starlette plumbs ``partitioned`` through to ``http.cookies.Morsel``,
    # which only recognized the attribute starting in Python 3.14. We need
    # the flag on 3.12/3.13 too, so patch the ``Set-Cookie`` header in
    # place. Only meaningful when Secure is set — browsers ignore
    # Partitioned on non-Secure cookies.
    if secure:
        _append_partitioned_to_last_set_cookie(response)


def _append_partitioned_to_last_set_cookie(response: Response) -> None:
    """Append ``; Partitioned`` to the most recent Set-Cookie header.

    ``MutableHeaders`` doesn't expose an "edit by name" helper for
    duplicate-allowed headers, and we need to be careful not to clobber
    any other Set-Cookie headers a parent middleware might have queued.
    """
    raw = response.raw_headers
    for idx in range(len(raw) - 1, -1, -1):
        name, value = raw[idx]
        if name.lower() == b"set-cookie" and value.startswith(
            WORKSPACE_SESSION_COOKIE_NAME.encode("latin-1") + b"="
        ):
            if b"partitioned" not in value.lower():
                raw[idx] = (name, value + b"; Partitioned")
            return


@auth_router.post(
    "/workspace-session",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Cookie set"},
        401: {"description": "Missing or invalid X-Session-API-Key header"},
    },
)
async def create_workspace_session(request: Request, response: Response) -> Response:
    """Mint a workspace-scoped session cookie.

    Caller must already be authenticated by the ``X-Session-API-Key``
    header (enforced by the parent router's dependency). The cookie value
    is the validated session API key itself; it is HttpOnly so JS in
    workspace HTML cannot read it back.
    """
    session_api_key = request.headers.get("x-session-api-key", "")
    _set_workspace_cookie(
        response,
        value=session_api_key,
        secure=_request_is_secure_context(request),
        max_age=_COOKIE_MAX_AGE_SECONDS,
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@auth_router.delete(
    "/workspace-session",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={204: {"description": "Cookie cleared"}},
)
async def delete_workspace_session(request: Request, response: Response) -> Response:
    """Clear the workspace session cookie.

    Browsers identify cookies by ``(name, domain, path)``; the deletion
    cookie must therefore share the original cookie's attributes. We
    overwrite with an empty value and ``max_age=0`` so the browser drops
    it immediately.
    """
    _set_workspace_cookie(
        response,
        value="",
        secure=_request_is_secure_context(request),
        max_age=0,
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
