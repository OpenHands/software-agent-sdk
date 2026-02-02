"""OpenAI subscription-based authentication via OAuth.

This module implements OAuth PKCE flow for authenticating with OpenAI's ChatGPT
service, allowing users with ChatGPT Plus/Pro subscriptions to use Codex models
without consuming API credits.

Uses authlib for OAuth handling and aiohttp for the callback server.
"""

from __future__ import annotations

import asyncio
import platform
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode

from aiohttp import web
from authlib.common.security import generate_token
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from httpx import AsyncClient, Client

from openhands.sdk.llm.auth.credentials import (
    CredentialStore,
    OAuthCredentials,
    get_credentials_dir,
)
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM

# Supported vendors for subscription-based authentication.
# Add new vendors here as they become supported.
SupportedVendor = Literal["openai"]

logger = get_logger(__name__)

# =========================================================================
# Consent banner constants
# =========================================================================

CONSENT_BANNER = """\
Signing in with ChatGPT uses your ChatGPT account. By continuing, you confirm \
you are a ChatGPT End User and are subject to OpenAI's Terms of Use.
https://openai.com/policies/terms-of-use/
"""

CONSENT_MARKER_FILENAME = ".chatgpt_consent_acknowledged"


def _get_consent_marker_path() -> Path:
    """Get the path to the consent acknowledgment marker file."""
    return get_credentials_dir() / CONSENT_MARKER_FILENAME


def _has_acknowledged_consent() -> bool:
    """Check if the user has previously acknowledged the consent disclaimer."""
    return _get_consent_marker_path().exists()


def _mark_consent_acknowledged() -> None:
    """Mark that the user has acknowledged the consent disclaimer."""
    marker_path = _get_consent_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.touch()


def _display_consent_and_confirm() -> bool:
    """Display consent banner and get user confirmation.

    Returns:
        True if user confirms, False otherwise.

    Raises:
        RuntimeError: If running in non-interactive mode without prior consent.
    """
    is_first_time = not _has_acknowledged_consent()

    # Always show the consent banner
    print("\n" + "=" * 70)
    print(CONSENT_BANNER)
    print("=" * 70 + "\n")

    # Check if we're in an interactive terminal
    if not sys.stdin.isatty():
        if is_first_time:
            raise RuntimeError(
                "Cannot proceed with ChatGPT sign-in: running in non-interactive mode "
                "and consent has not been previously acknowledged. Please run "
                "interactively first to acknowledge the terms."
            )
        # Non-interactive but consent was previously given - proceed
        logger.info("Non-interactive mode: using previously acknowledged consent")
        return True

    # Interactive mode: prompt for confirmation
    try:
        response = input("Do you want to continue? [y/N]: ").strip().lower()
        if response in ("y", "yes"):
            if is_first_time:
                _mark_consent_acknowledged()
            return True
        return False
    except (EOFError, KeyboardInterrupt):
        print()  # Newline after ^C
        return False


# OAuth configuration for OpenAI Codex
# This is a public client ID for OpenAI's OAuth flow (safe to commit)
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
CODEX_API_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_OAUTH_PORT = 1455
OAUTH_TIMEOUT_SECONDS = 300  # 5 minutes
JWKS_CACHE_TTL_SECONDS = 3600  # 1 hour

# Models available via ChatGPT subscription (not API)
OPENAI_CODEX_MODELS = frozenset(
    {
        "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini",
        "gpt-5.2",
        "gpt-5.2-codex",
    }
)


# Thread-safe JWKS cache
class _JWKSCache:
    """Thread-safe cache for OpenAI's JWKS (JSON Web Key Set)."""

    def __init__(self) -> None:
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0
        self._lock = threading.Lock()

    def get_key_set(self) -> Any:
        """Get the JWKS, fetching from OpenAI if cache is stale or empty.

        Returns:
            KeySet for verifying JWT signatures.

        Raises:
            RuntimeError: If JWKS cannot be fetched.
        """
        with self._lock:
            now = time.time()
            if not self._keys or (now - self._fetched_at) > JWKS_CACHE_TTL_SECONDS:
                self._fetch_jwks()
            return JsonWebKey.import_key_set(self._keys)

    def _fetch_jwks(self) -> None:
        """Fetch JWKS from OpenAI's well-known endpoint."""
        try:
            with Client(timeout=10) as client:
                response = client.get(JWKS_URL)
                response.raise_for_status()
                self._keys = response.json()
                self._fetched_at = time.time()
                logger.debug(
                    f"Fetched JWKS from OpenAI: {len(self._keys.get('keys', []))} keys"
                )
        except Exception as e:
            raise RuntimeError(f"Failed to fetch OpenAI JWKS: {e}") from e

    def clear(self) -> None:
        """Clear the cache (useful for testing)."""
        with self._lock:
            self._keys = {}
            self._fetched_at = 0


_jwks_cache = _JWKSCache()


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and challenge using authlib."""
    verifier = generate_token(43)
    challenge = create_s256_code_challenge(verifier)
    return verifier, challenge


def _extract_chatgpt_account_id(access_token: str) -> str | None:
    """Extract chatgpt_account_id from JWT access token with signature verification.

    Verifies the JWT signature using OpenAI's published JWKS before extracting
    claims. This prevents attacks where a manipulated token could be injected
    through OAuth callback interception.

    Args:
        access_token: The JWT access token from OAuth flow

    Returns:
        The chatgpt_account_id if found and signature is valid, None otherwise
    """
    try:
        # Fetch JWKS and verify JWT signature
        key_set = _jwks_cache.get_key_set()
        claims = jwt.decode(access_token, key_set)

        # Validate standard claims (issuer)
        claims.validate()

        # Extract account ID from nested structure
        auth_info = claims.get("https://api.openai.com/auth", {})
        account_id = auth_info.get("chatgpt_account_id")

        if account_id:
            logger.debug(f"Extracted chatgpt_account_id: {account_id}")
            return account_id
        else:
            logger.warning("chatgpt_account_id not found in JWT payload")
            return None

    except JoseError as e:
        logger.warning(f"JWT signature verification failed: {e}")
        return None
    except RuntimeError as e:
        # JWKS fetch failed - log but don't crash
        logger.warning(f"Could not verify JWT: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to decode JWT: {e}")
        return None


def _build_authorize_url(redirect_uri: str, code_challenge: str, state: str) -> str:
    """Build the OAuth authorization URL."""
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

        Raises:
            RuntimeError: If token refresh fails.
        """
        creds = self.get_credentials()
        if creds is None:
            return None

        if not creds.is_expired():
            return creds

        logger.info("Refreshing OpenAI access token")
        tokens = await _refresh_access_token(creds.refresh_token)
        updated = self._credential_store.update_tokens(
            vendor=self.vendor,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_in=tokens.get("expires_in", 3600),
        )
        return updated

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
            try:
                await site.start()
            except OSError as exc:
                if "address already in use" in str(exc).lower():
                    raise RuntimeError(
                        "OAuth callback server port "
                        f"{self._oauth_port} is already in use. "
                        "Please free the port or set a different one via "
                        "OPENHANDS_OAUTH_PORT."
                    ) from exc
                raise

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
            instructions: Optional instructions for the Codex model.
            **llm_kwargs: Additional arguments to pass to LLM constructor.

        Returns:
            An LLM instance configured for Codex access.

        Raises:
            ValueError: If the model is not supported or no credentials available.
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

        account_id = _extract_chatgpt_account_id(creds.access_token)
        if not account_id:
            logger.warning(
                "Could not extract chatgpt_account_id from access token. "
                "API requests may fail."
            )

        # Build extra_body with Codex-specific params
        extra_body: dict[str, Any] = {"store": False}
        if instructions:
            extra_body["instructions"] = instructions
        if "litellm_extra_body" in llm_kwargs:
            extra_body.update(llm_kwargs.pop("litellm_extra_body"))

        # Build headers matching OpenAI's official Codex CLI
        extra_headers: dict[str, str] = {
            "originator": "codex_cli_rs",
            "OpenAI-Beta": "responses=experimental",
            "User-Agent": f"openhands-sdk ({platform.system()}; {platform.machine()})",
        }
        if account_id:
            extra_headers["chatgpt-account-id"] = account_id

        # Codex API requires streaming and doesn't support temperature/max_output_tokens
        llm = LLM(
            model=f"openai/{model}",
            base_url=CODEX_API_ENDPOINT.rsplit("/", 1)[0],
            api_key=creds.access_token,
            extra_headers=extra_headers,
            litellm_extra_body=extra_body,
            temperature=None,
            max_output_tokens=None,
            stream=True,
            **llm_kwargs,
        )
        llm._is_subscription = True
        # Ensure these stay None even if model info tried to set them
        llm.max_output_tokens = None
        llm.temperature = None
        return llm


async def subscription_login_async(
    vendor: SupportedVendor = "openai",
    model: str = "gpt-5.2-codex",
    force_login: bool = False,
    open_browser: bool = True,
    skip_consent: bool = False,
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
        skip_consent: If True, skip the consent prompt (for programmatic use
            where consent has been obtained through other means).
        **llm_kwargs: Additional arguments to pass to LLM constructor.

    Returns:
        An LLM instance configured for subscription access.

    Raises:
        ValueError: If the vendor is not supported.
        RuntimeError: If authentication fails or user declines consent.

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

    # Display consent banner and get confirmation before login
    if not skip_consent:
        if not _display_consent_and_confirm():
            raise RuntimeError("User declined to continue with ChatGPT sign-in")

    # Perform login
    creds = await auth.login(open_browser=open_browser)
    return auth.create_llm(model=model, credentials=creds, **llm_kwargs)


def subscription_login(
    vendor: SupportedVendor = "openai",
    model: str = "gpt-5.2-codex",
    force_login: bool = False,
    open_browser: bool = True,
    skip_consent: bool = False,
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
            skip_consent=skip_consent,
            **llm_kwargs,
        )
    )


# =========================================================================
# Message transformation utilities for subscription mode
# =========================================================================

DEFAULT_SYSTEM_MESSAGE = (
    "You are OpenHands agent, a helpful AI assistant that can interact "
    "with a computer to solve tasks."
)


def inject_system_prefix(
    input_items: list[dict[str, Any]], prefix_content: dict[str, Any]
) -> None:
    """Inject system prefix into the first user message, or create one.

    This modifies input_items in place.

    Args:
        input_items: List of input items (messages) to modify.
        prefix_content: The content dict to prepend
            (e.g., {"type": "input_text", "text": "..."}).
    """
    for item in input_items:
        if item.get("type") == "message" and item.get("role") == "user":
            content = item.get("content")
            if not isinstance(content, list):
                content = [content] if content else []
            item["content"] = [prefix_content] + content
            return

    # No user message found, create a synthetic one
    input_items.insert(0, {"role": "user", "content": [prefix_content]})


def transform_for_subscription(
    system_chunks: list[str], input_items: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Transform messages for Codex subscription transport.

    Codex subscription endpoints reject complex/long `instructions`, so we:
    1. Use a minimal default instruction string
    2. Prepend system prompts to the first user message
    3. Normalize message format to match OpenCode's Codex client

    Args:
        system_chunks: List of system prompt strings to merge.
        input_items: List of input items (messages) to transform.

    Returns:
        A tuple of (instructions, normalized_input_items).
    """
    # Prepend system prompts to first user message
    if system_chunks:
        merged = "\n\n---\n\n".join(system_chunks)
        prefix_content = {
            "type": "input_text",
            "text": f"Context (system prompt):\n{merged}\n\n",
        }
        inject_system_prefix(input_items, prefix_content)

    # Normalize: {"type": "message", ...} -> {"role": ..., "content": ...}
    normalized = [
        {"role": item.get("role"), "content": item.get("content") or []}
        if item.get("type") == "message"
        else item
        for item in input_items
    ]
    return DEFAULT_SYSTEM_MESSAGE, normalized
