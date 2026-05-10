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
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

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


def _request_is_https(request: Request) -> bool:
    """Detect HTTPS, honoring ``X-Forwarded-Proto`` set by trusted proxies.

    We can't rely on ``request.url.scheme`` alone because the agent server
    is typically behind nginx which terminates TLS and forwards plain HTTP.
    Nginx (and the canvas ingress) set ``X-Forwarded-Proto`` accordingly.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").lower()
    if forwarded:
        return forwarded.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


def _set_workspace_cookie(
    response: Response, *, value: str, secure: bool, max_age: int
) -> None:
    """Issue the workspace session cookie.

    Cross-site iframe support requires ``SameSite=None; Secure``. Modern
    Chrome additionally requires ``Partitioned`` (CHIPS) for cookies set
    in third-party contexts; without it, the cookie may be silently
    dropped under third-party-cookie phase-out.

    We always set ``SameSite=None`` so the same cookie works for both
    same-site and cross-site iframes, and we always set ``HttpOnly`` so
    JS in workspace HTML can't read it.
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
    secure = _request_is_https(request)
    _set_workspace_cookie(
        response,
        value=session_api_key,
        secure=secure,
        max_age=_COOKIE_MAX_AGE_SECONDS,
    )
    if not secure:
        # SameSite=None requires Secure in modern browsers, so a non-HTTPS
        # cookie will be rejected by Chrome/Firefox. Loudly warn so dev
        # users investigating "my iframe is 401" can find the cause.
        logger.warning(
            "Issuing workspace-session cookie over a non-HTTPS connection; "
            "browsers will reject SameSite=None cookies without Secure. "
            "Run the agent server behind a TLS-terminating proxy that sets "
            "X-Forwarded-Proto=https for cross-site iframe support."
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
    secure = _request_is_https(request)
    _set_workspace_cookie(response, value="", secure=secure, max_age=0)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
