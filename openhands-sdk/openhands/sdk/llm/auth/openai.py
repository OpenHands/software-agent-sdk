"""OpenAI subscription-based authentication via OAuth.

This module implements OAuth PKCE flow for authenticating with OpenAI's ChatGPT
service, allowing users with ChatGPT Plus/Pro subscriptions to use Codex models
without consuming API credits.

Uses authlib for OAuth handling and aiohttp for the callback server.
"""

from __future__ import annotations

import asyncio
import os
import time
import webbrowser
from typing import TYPE_CHECKING, Any

from aiohttp import web
from authlib.common.security import generate_token
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from httpx import AsyncClient

from openhands.sdk.llm.auth.credentials import CredentialStore, OAuthCredentials
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM

logger = get_logger(__name__)

# OAuth configuration for OpenAI Codex
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
CODEX_API_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_OAUTH_PORT = 1455
OAUTH_TIMEOUT_SECONDS = 300  # 5 minutes

# Models available via ChatGPT subscription (not API)
OPENAI_CODEX_MODELS = frozenset(
    {
        "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini",
        "gpt-5.2",
        "gpt-5.2-codex",
    }
)


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and challenge using authlib."""
    verifier = generate_token(43)
    challenge = create_s256_code_challenge(verifier)
    return verifier, challenge


def _build_authorize_url(redirect_uri: str, code_challenge: str, state: str) -> str:
    """Build the OAuth authorization URL."""
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": "openhands",
    }
    return f"{ISSUER}/oauth/authorize?{urlencode(params)}"


async def _exchange_code_for_tokens(
    code: str, redirect_uri: str, code_verifier: str
) -> dict[str, Any]:
    """Exchange authorization code for tokens."""
    async with AsyncClient() as client:
        response = await client.post(
            f"{ISSUER}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not response.is_success:
            raise RuntimeError(f"Token exchange failed: {response.status_code}")
        return response.json()


async def _refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Refresh the access token using a refresh token."""
    async with AsyncClient() as client:
        response = await client.post(
            f"{ISSUER}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not response.is_success:
            raise RuntimeError(f"Token refresh failed: {response.status_code}")
        return response.json()


# HTML templates for OAuth callback
_HTML_SUCCESS = """<!DOCTYPE html>
<html>
<head>
  <title>OpenHands - Authorization Successful</title>
  <style>
    body { font-family: system-ui, sans-serif; display: flex;
           justify-content: center; align-items: center; height: 100vh;
           margin: 0; background: #1a1a2e; color: #eee; }
    .container { text-align: center; padding: 2rem; }
    h1 { color: #4ade80; }
    p { color: #aaa; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Authorization Successful</h1>
    <p>You can close this window and return to OpenHands.</p>
  </div>
  <script>setTimeout(() => window.close(), 2000);</script>
</body>
</html>"""

_HTML_ERROR = """<!DOCTYPE html>
<html>
<head>
  <title>OpenHands - Authorization Failed</title>
  <style>
    body { font-family: system-ui, sans-serif; display: flex;
           justify-content: center; align-items: center; height: 100vh;
           margin: 0; background: #1a1a2e; color: #eee; }
    .container { text-align: center; padding: 2rem; }
    h1 { color: #f87171; }
    p { color: #aaa; }
    .error { color: #fca5a5; font-family: monospace; margin-top: 1rem;
             padding: 1rem; background: rgba(248,113,113,0.1);
             border-radius: 0.5rem; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Authorization Failed</h1>
    <p>An error occurred during authorization.</p>
    <div class="error">{error}</div>
  </div>
</body>
</html>"""


class OpenAISubscriptionAuth:
    """Handle OAuth authentication for OpenAI ChatGPT subscription access."""

    def __init__(
        self,
        credential_store: CredentialStore | None = None,
        oauth_port: int = DEFAULT_OAUTH_PORT,
    ):
        """Initialize the OpenAI subscription auth handler.

        Args:
            credential_store: Optional custom credential store.
            oauth_port: Port for the local OAuth callback server.
        """
        self._credential_store = credential_store or CredentialStore()
        self._oauth_port = oauth_port

    @property
    def vendor(self) -> str:
        """Get the vendor name."""
        return "openai"

    def get_credentials(self) -> OAuthCredentials | None:
        """Get stored credentials if they exist."""
        return self._credential_store.get(self.vendor)

    def has_valid_credentials(self) -> bool:
        """Check if valid (non-expired) credentials exist."""
        creds = self.get_credentials()
        return creds is not None and not creds.is_expired()

    async def refresh_if_needed(self) -> OAuthCredentials | None:
        """Refresh credentials if they are expired.

        Returns:
            Updated credentials, or None if no credentials exist.
        """
        creds = self.get_credentials()
        if creds is None:
            return None

        if not creds.is_expired():
            return creds

        logger.info("Refreshing OpenAI access token")
        try:
            tokens = await _refresh_access_token(creds.refresh_token)
            updated = self._credential_store.update_tokens(
                vendor=self.vendor,
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token"),
                expires_in=tokens.get("expires_in", 3600),
            )
            return updated
        except Exception as e:
            logger.warning(f"Failed to refresh token: {e}")
            self._credential_store.delete(self.vendor)
            return None

    async def login(self, open_browser: bool = True) -> OAuthCredentials:
        """Perform OAuth login flow.

        This starts a local HTTP server to handle the OAuth callback,
        opens the browser for user authentication, and waits for the
        callback with the authorization code.

        Args:
            open_browser: Whether to automatically open the browser.

        Returns:
            The obtained OAuth credentials.

        Raises:
            RuntimeError: If the OAuth flow fails or times out.
        """
        code_verifier, code_challenge = _generate_pkce()
        state = generate_token(32)
        redirect_uri = f"http://localhost:{self._oauth_port}/auth/callback"
        auth_url = _build_authorize_url(redirect_uri, code_challenge, state)

        # Future to receive callback result
        callback_future: asyncio.Future[dict[str, Any]] = asyncio.Future()

        # Create aiohttp app for callback
        app = web.Application()

        async def handle_callback(request: web.Request) -> web.Response:
            params = request.query

            if "error" in params:
                error_msg = params.get("error_description", params["error"])
                if not callback_future.done():
                    callback_future.set_exception(RuntimeError(error_msg))
                return web.Response(
                    text=_HTML_ERROR.format(error=error_msg),
                    content_type="text/html",
                )

            code = params.get("code")
            if not code:
                error_msg = "Missing authorization code"
                if not callback_future.done():
                    callback_future.set_exception(RuntimeError(error_msg))
                return web.Response(
                    text=_HTML_ERROR.format(error=error_msg),
                    content_type="text/html",
                    status=400,
                )

            if params.get("state") != state:
                error_msg = "Invalid state - potential CSRF attack"
                if not callback_future.done():
                    callback_future.set_exception(RuntimeError(error_msg))
                return web.Response(
                    text=_HTML_ERROR.format(error=error_msg),
                    content_type="text/html",
                    status=400,
                )

            try:
                tokens = await _exchange_code_for_tokens(
                    code, redirect_uri, code_verifier
                )
                if not callback_future.done():
                    callback_future.set_result(tokens)
                return web.Response(text=_HTML_SUCCESS, content_type="text/html")
            except Exception as e:
                if not callback_future.done():
                    callback_future.set_exception(e)
                return web.Response(
                    text=_HTML_ERROR.format(error=str(e)),
                    content_type="text/html",
                    status=500,
                )

        app.router.add_get("/auth/callback", handle_callback)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self._oauth_port)

        try:
            await site.start()
            logger.debug(f"OAuth callback server started on port {self._oauth_port}")

            if open_browser:
                logger.info("Opening browser for OpenAI authentication...")
                webbrowser.open(auth_url)
            else:
                logger.info(
                    f"Please open the following URL in your browser:\n{auth_url}"
                )

            try:
                tokens = await asyncio.wait_for(
                    callback_future, timeout=OAUTH_TIMEOUT_SECONDS
                )
            except TimeoutError:
                raise RuntimeError(
                    "OAuth callback timeout - authorization took too long"
                )

            expires_at = int(time.time() * 1000) + (
                tokens.get("expires_in", 3600) * 1000
            )
            credentials = OAuthCredentials(
                vendor=self.vendor,
                access_token=tokens["access_token"],
                refresh_token=tokens["refresh_token"],
                expires_at=expires_at,
            )
            self._credential_store.save(credentials)
            logger.info("OpenAI OAuth login successful")
            return credentials

        finally:
            await runner.cleanup()

    def logout(self) -> bool:
        """Remove stored credentials.

        Returns:
            True if credentials were removed, False if none existed.
        """
        return self._credential_store.delete(self.vendor)

    def create_llm(
        self,
        model: str = "gpt-5.2-codex",
        credentials: OAuthCredentials | None = None,
        instructions: str | None = None,
        **llm_kwargs: Any,
    ) -> LLM:
        """Create an LLM instance configured for Codex subscription access.

        Args:
            model: The model to use (must be in OPENAI_CODEX_MODELS).
            credentials: OAuth credentials to use. If None, uses stored credentials.
            instructions: Optional instructions for the Codex model. This is sent
                as the 'instructions' field in the API request.
            **llm_kwargs: Additional arguments to pass to LLM constructor.

        Returns:
            An LLM instance configured for Codex access.

        Raises:
            ValueError: If the model is not supported or no credentials available.

        Note:
            The Codex API has specific requirements:
            - Uses 'instructions' field instead of system messages
            - Requires 'store: false' to not persist conversations
            - System prompts should be sent as user messages, not system role
        """
        from openhands.sdk.llm.llm import LLM

        if model not in OPENAI_CODEX_MODELS:
            raise ValueError(
                f"Model '{model}' is not supported for subscription access. "
                f"Supported models: {', '.join(sorted(OPENAI_CODEX_MODELS))}"
            )

        creds = credentials or self.get_credentials()
        if creds is None:
            raise ValueError(
                "No credentials available. Call login() first or provide credentials."
            )

        uname = os.uname()
        user_agent = f"openhands-sdk ({uname.sysname}; {uname.machine})"

        # Codex-specific extra_body parameters
        extra_body: dict[str, Any] = {
            "store": False,  # Don't persist conversations
        }
        if instructions:
            extra_body["instructions"] = instructions

        # Merge with any user-provided extra_body
        if "litellm_extra_body" in llm_kwargs:
            extra_body = {**extra_body, **llm_kwargs.pop("litellm_extra_body")}

        return LLM(
            model=f"openai/{model}",
            base_url=CODEX_API_ENDPOINT.rsplit("/", 1)[0],
            api_key=creds.access_token,
            extra_headers={
                "originator": "openhands",
                "User-Agent": user_agent,
            },
            temperature=None,
            max_output_tokens=None,  # Codex doesn't support this
            litellm_extra_body=extra_body,
            **llm_kwargs,
        )


async def subscription_login_async(
    vendor: str = "openai",
    model: str = "gpt-5.2-codex",
    force_login: bool = False,
    open_browser: bool = True,
    **llm_kwargs: Any,
) -> LLM:
    """Authenticate with a subscription and return an LLM instance.

    This is the main entry point for subscription-based LLM access.
    It handles credential caching, token refresh, and login flow.

    Args:
        vendor: The vendor/provider (currently only "openai" is supported).
        model: The model to use.
        force_login: If True, always perform a fresh login.
        open_browser: Whether to automatically open the browser for login.
        **llm_kwargs: Additional arguments to pass to LLM constructor.

    Returns:
        An LLM instance configured for subscription access.

    Raises:
        ValueError: If the vendor is not supported.
        RuntimeError: If authentication fails.

    Example:
        >>> import asyncio
        >>> from openhands.sdk.llm.auth import subscription_login_async
        >>> llm = asyncio.run(subscription_login_async(model="gpt-5.2-codex"))
    """
    if vendor != "openai":
        raise ValueError(
            f"Vendor '{vendor}' is not supported. Only 'openai' is supported."
        )

    auth = OpenAISubscriptionAuth()

    # Check for existing valid credentials
    if not force_login:
        creds = await auth.refresh_if_needed()
        if creds is not None:
            logger.info("Using existing OpenAI credentials")
            return auth.create_llm(model=model, credentials=creds, **llm_kwargs)

    # Perform login
    creds = await auth.login(open_browser=open_browser)
    return auth.create_llm(model=model, credentials=creds, **llm_kwargs)


def subscription_login(
    vendor: str = "openai",
    model: str = "gpt-5.2-codex",
    force_login: bool = False,
    open_browser: bool = True,
    **llm_kwargs: Any,
) -> LLM:
    """Synchronous wrapper for subscription_login_async.

    See subscription_login_async for full documentation.
    """
    return asyncio.run(
        subscription_login_async(
            vendor=vendor,
            model=model,
            force_login=force_login,
            open_browser=open_browser,
            **llm_kwargs,
        )
    )
