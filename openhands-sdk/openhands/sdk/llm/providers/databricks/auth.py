"""Databricks FMAPI authentication strategies.

Supports all 5 PWAF-required auth paths:
  U2M     — OAuth browser PKCE flow (PWAF primary interactive auth).
             Provider receives StoredU2MTokens from app layer; manages refresh only.
  M2M     — OAuth client credentials (PWAF primary service auth).
             M2MTokenProvider fetches/refreshes tokens with threading.Lock.
  PAT     — Personal Access Token (additional option only per PWAF).
  PROFILE — Databricks CLI profile (~/.databrickscfg). Requires databricks-sdk.
  UNIFIED — databricks-sdk unified auth chain (workload identity, Azure AD, etc.).
             Requires databricks-sdk.

Auth priority (PWAF compliant): U2M > M2M > PAT > PROFILE > UNIFIED.

Client ID distinction (critical):
  DATABRICKS_CLIENT_ID      — M2M service principal (grant_type=client_credentials)
  DATABRICKS_U2M_CLIENT_ID  — Custom OAuth app for browser login (PKCE flow)
  Using M2M client_id for U2M → "OAuth application not available" from Databricks.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

import httpx
from litellm.exceptions import AuthenticationError

from openhands.sdk.llm.providers.databricks.models import StoredU2MTokens
from openhands.sdk.llm.providers.databricks.utils import (
    USER_AGENT,
    normalize_host,
    validate_databricks_config,
)

if TYPE_CHECKING:
    from openhands.sdk.llm.providers.databricks.llm import DatabricksLLM

logger = logging.getLogger(__name__)


class AuthStrategy(str, Enum):
    """Auth strategy discriminator. Used in resolve_credentials() priority chain."""

    U2M = "u2m"        # OAuth browser PKCE — PWAF primary interactive auth
    M2M = "m2m"        # OAuth client credentials — PWAF primary service auth
    PAT = "pat"        # Personal Access Token — additional option only per PWAF
    PROFILE = "profile"  # Databricks CLI profile (~/.databrickscfg)
    UNIFIED = "unified"  # databricks-sdk unified auth chain (fallback)


@dataclass
class DatabricksCredentials:
    """Resolved Databricks credentials ready for use in API calls.

    get_token is always synchronous — threading.Lock used internally for M2M/U2M.
    auth_method is a plain string logged for observability (never the token value).
    """

    host: str
    get_token: Callable[[], str]
    auth_method: str = "unknown"   # "u2m" / "m2m" / "pat" / "profile" / "unified"


# ---------------------------------------------------------------------------
# M2M token provider — thread-safe via threading.Lock (not asyncio)
# ---------------------------------------------------------------------------

class M2MTokenProvider:
    """Thread-safe OAuth client credentials token provider.

    P0-3: constructor accepts host, client_id, client_secret (was missing in P3 plan).
    Uses double-checked locking to ensure exactly one _fetch_new_token() call under
    concurrent pressure.
    """

    def __init__(self, host: str, client_id: str, client_secret: str) -> None:
        self._host = host
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()   # threading, NOT asyncio

    def get_token(self) -> str:
        """Return a valid access token, refreshing proactively if nearing expiry."""
        # Fast path: check without acquiring lock
        if self._token and time.time() < self._expires_at - 300:
            return self._token
        with self._lock:
            # Double-check inside lock to prevent thundering herd
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            self._token, self._expires_at = self._fetch_new_token()
            return self._token

    def _fetch_new_token(self) -> tuple[str, float]:
        """Fetch a new M2M access token via client credentials grant."""
        resp = httpx.post(
            f"{self._host}/oidc/v1/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "all-apis",   # required by Databricks OIDC
            },
            headers={"User-Agent": USER_AGENT},  # PWAF: UA on ALL Databricks HTTP
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        expires_at = time.time() + data.get("expires_in", 3600)
        return data["access_token"], expires_at


# ---------------------------------------------------------------------------
# Main credential resolver
# ---------------------------------------------------------------------------

def resolve_credentials(llm: "DatabricksLLM") -> DatabricksCredentials:
    """Resolve auth strategy and return DatabricksCredentials.

    Priority (PWAF: OAuth primary, PAT additional only):
      1. U2M     stored_u2m_tokens is set (user completed browser login)
      2. M2M     databricks_client_id + databricks_client_secret both set
      3. PAT     api_key is set
      4. PROFILE databricks_profile named
      5. UNIFIED databricks-sdk auth chain (fallback)

    Host resolution: ``databricks_host`` (workspace) is required for U2M /
    M2M / PROFILE / UNIFIED (workspace OAuth) and when
    ``databricks_metadata_probe=True``. PAT only needs *some* host for the
    FM client to route to — either ``databricks_host`` (canonical) or
    ``databricks_ai_gateway_host`` (override). The validator in
    ``DatabricksLLM`` enforces that at least one is set.
    """
    stored = llm.stored_u2m_tokens
    host = (
        llm.databricks_host
        or llm.base_url
        or (stored.host if stored else None)
    )
    if host:
        host = normalize_host(host)

    # Path 1: U2M — highest priority (user already browser-logged in)
    if stored:
        if not host:
            raise ValueError("databricks_host is required for U2M auth.")
        validate_databricks_config(host, AuthStrategy.U2M, stored_tokens=stored)
        return _resolve_u2m(host, stored)

    # Path 2: M2M — service principal client credentials
    if llm.databricks_client_id and llm.databricks_client_secret:
        if not host:
            raise ValueError("databricks_host is required for M2M auth.")
        validate_databricks_config(
            host,
            AuthStrategy.M2M,
            client_id=llm.databricks_client_id,
            client_secret=llm.databricks_client_secret.get_secret_value(),
        )
        return _resolve_m2m(host, llm)

    # Path 3: PAT — token is sent directly to the AI Gateway, no workspace
    # host needed. credentials.host is left empty so any accidental use
    # surfaces clearly.
    if llm.api_key:
        token = (
            llm.api_key.get_secret_value()
            if hasattr(llm.api_key, "get_secret_value")
            else str(llm.api_key)
        )
        logger.info("databricks_auth_resolved", extra={"method": "pat"})
        return DatabricksCredentials(
            host=host or "", get_token=lambda: token, auth_method="pat"
        )

    # Path 4: Named CLI profile
    if llm.databricks_profile:
        if not host:
            raise ValueError(
                "databricks_host is required when using databricks_profile."
            )
        return _resolve_profile(host, llm.databricks_profile)

    # Path 5: SDK unified auth chain (workload identity, Azure AD, ~/.databrickscfg)
    if not host:
        raise ValueError(
            "databricks_host is required for unified-SDK auth."
        )
    return _resolve_sdk_auth(host)


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def _resolve_u2m(host: str, stored: StoredU2MTokens) -> DatabricksCredentials:
    """U2M: return current access token, refreshing silently via refresh_token.

    Proactive refresh: 5 minutes before expiry (300s buffer).
    Uses threading.Lock for thread safety in the synchronous call path.
    """
    lock = threading.Lock()
    state: dict[str, object] = {
        "token": stored.access_token,
        "expires_at": stored.expires_at,
    }

    def get_token() -> str:
        # Fast path — no lock needed
        if time.time() < float(state["expires_at"]) - 300:
            return str(state["token"])
        with lock:
            if time.time() < float(state["expires_at"]) - 300:
                return str(state["token"])
            remaining = float(state["expires_at"]) - time.time()
            logger.info(
                "databricks_u2m_token_refresh",
                extra={"remaining_s": round(remaining, 1)},
            )
            resp = httpx.post(
                f"{host}/oidc/v1/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": stored.refresh_token,
                    "client_id": stored.client_id,
                    # No client_secret for PKCE (public client)
                },
                headers={"User-Agent": USER_AGENT},  # PWAF: UA on token endpoint
                timeout=15.0,
            )
            if not resp.is_success:
                raise AuthenticationError(
                    f"U2M token refresh failed [{resp.status_code}]. "
                    "Re-authenticate at /auth/databricks/initiate.",
                    model="",
                    llm_provider="databricks",
                )
            data = resp.json()
            state["token"] = data["access_token"]
            state["expires_at"] = time.time() + data.get("expires_in", 3600)
            logger.info("databricks_u2m_token_refreshed", extra={"method": "u2m"})
            return str(state["token"])

    logger.info("databricks_auth_resolved", extra={"method": "u2m"})
    return DatabricksCredentials(host=host, get_token=get_token, auth_method="u2m")


def _resolve_m2m(host: str, llm: "DatabricksLLM") -> DatabricksCredentials:
    """M2M: client credentials grant via M2MTokenProvider."""
    assert llm.databricks_client_secret is not None  # validated in resolve_credentials
    provider = M2MTokenProvider(
        host=host,
        client_id=llm.databricks_client_id,  # type: ignore[arg-type]
        client_secret=llm.databricks_client_secret.get_secret_value(),
    )
    logger.info("databricks_auth_resolved", extra={"method": "m2m"})
    return DatabricksCredentials(
        host=host, get_token=provider.get_token, auth_method="m2m"
    )


def _resolve_profile(host: str, profile: str) -> DatabricksCredentials:
    """PROFILE: Databricks CLI profile via databricks-sdk. Requires optional dep.

    The import check is deferred into ``get_token()`` so that saving settings
    succeeds even if ``databricks-sdk`` is not yet installed; the clear error
    with install instructions surfaces only when the agent first makes an API
    call.
    """
    client_holder: dict[str, object] = {}
    lock = threading.Lock()

    def get_token() -> str:
        try:
            from databricks.sdk import WorkspaceClient as _WC
        except ImportError:
            raise ImportError(
                "PROFILE auth requires the 'databricks-sdk' package.\n"
                "Install it:  pip install databricks-sdk\n"
                f"Then verify: databricks auth profiles  "
                f"(profile '{profile}' must appear)"
            ) from None

        client = client_holder.get("client")
        if client is None:
            with lock:
                client = client_holder.get("client")
                if client is None:
                    client = _WC(host=host, profile=profile)
                    client_holder["client"] = client
        auth_header = client.config.authenticate()["Authorization"]  # type: ignore[attr-defined]
        return auth_header.split(" ", 1)[1] if " " in auth_header else auth_header

    logger.info("databricks_auth_resolved", extra={"method": "profile", "profile": profile})
    return DatabricksCredentials(host=host, get_token=get_token, auth_method="profile")


def _resolve_sdk_auth(host: str) -> DatabricksCredentials:
    """UNIFIED: databricks-sdk auth chain (workload identity, Azure AD, ~/.databrickscfg).

    The import check is deferred into ``get_token()`` so that saving settings
    succeeds even if ``databricks-sdk`` is not yet installed; the clear error
    with install + pre-login instructions surfaces only when the agent first
    makes an API call.

    Pre-requisites (outside the agent):
      1. pip install databricks-sdk
      2. databricks auth login --host <workspace_host>
    """
    client_holder: dict[str, object] = {}
    lock = threading.Lock()

    def get_token() -> str:
        try:
            from databricks.sdk import WorkspaceClient as _WC
        except ImportError:
            raise ImportError(
                "Browser-SSO / unified auth requires the 'databricks-sdk' package.\n"
                "  Step 1 — install: pip install databricks-sdk\n"
                f"  Step 2 — login:   databricks auth login --host {host}\n"
                "Re-run the agent after completing both steps."
            ) from None

        client = client_holder.get("client")
        if client is None:
            with lock:
                client = client_holder.get("client")
                if client is None:
                    client = _WC(host=host)
                    client_holder["client"] = client
        auth_header = client.config.authenticate()["Authorization"]  # type: ignore[attr-defined]
        return auth_header.split(" ", 1)[1] if " " in auth_header else auth_header

    logger.info("databricks_auth_resolved", extra={"method": "unified"})
    return DatabricksCredentials(host=host, get_token=get_token, auth_method="unified")
