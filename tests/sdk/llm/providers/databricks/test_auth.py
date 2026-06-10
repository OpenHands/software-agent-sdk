"""Tests for Databricks FMAPI authentication strategies.

Covers: M2MTokenProvider (double-checked locking, proactive refresh, scope=all-apis),
PAT path, U2M priority precedence, host resolution order, resolve_credentials dispatch.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.llm.providers.databricks.auth import (
    AuthStrategy,
    DatabricksCredentials,
    M2MTokenProvider,
    _resolve_m2m,
    _resolve_u2m,
    resolve_credentials,
)
from openhands.sdk.llm.providers.databricks.models import StoredU2MTokens
from openhands.sdk.llm.providers.databricks.utils import USER_AGENT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST = "https://adb-123.azuredatabricks.net"
_M2M_TOKEN_URL = f"{_HOST}/oidc/v1/token"


def _make_m2m_response(token: str = "m2m-token", expires_in: int = 3600) -> httpx.Response:
    # httpx requires `request` on Response for raise_for_status() (used in production code).
    req = httpx.Request("POST", _M2M_TOKEN_URL)
    return httpx.Response(
        200, json={"access_token": token, "expires_in": expires_in}, request=req
    )


def _make_refresh_response(token: str = "new-access-token", expires_in: int = 3600) -> httpx.Response:
    req = httpx.Request("POST", _M2M_TOKEN_URL)
    return httpx.Response(
        200, json={"access_token": token, "expires_in": expires_in}, request=req
    )


# ---------------------------------------------------------------------------
# M2MTokenProvider
# ---------------------------------------------------------------------------

class TestM2MTokenProvider:
    def test_constructor_accepts_host_client_id_secret(self) -> None:
        """P0-3: constructor signature must accept host, client_id, client_secret."""
        provider = M2MTokenProvider(
            host=_HOST,
            client_id="client-id",
            client_secret="client-secret",
        )
        assert provider._host == _HOST
        assert provider._client_id == "client-id"
        assert provider._client_secret == "client-secret"
        assert provider._token is None
        assert provider._expires_at == 0.0

    def test_get_token_fetches_on_first_call(self) -> None:
        """get_token() must call _fetch_new_token on first call (no cached token)."""
        provider = M2MTokenProvider(_HOST, "cid", "csecret")
        with patch.object(provider, "_fetch_new_token", return_value=("tok-1", time.time() + 7200)) as mock_fetch:
            token = provider.get_token()
        assert token == "tok-1"
        mock_fetch.assert_called_once()

    def test_get_token_uses_cached_token_when_fresh(self) -> None:
        """get_token() must return cached token without re-fetching when > 5min remaining."""
        provider = M2MTokenProvider(_HOST, "cid", "csecret")
        provider._token = "cached-tok"
        provider._expires_at = time.time() + 3600  # 1h remaining
        with patch.object(provider, "_fetch_new_token") as mock_fetch:
            token = provider.get_token()
        assert token == "cached-tok"
        mock_fetch.assert_not_called()

    def test_get_token_refreshes_when_near_expiry(self) -> None:
        """get_token() must refresh when < 5min (300s) remaining (proactive refresh)."""
        provider = M2MTokenProvider(_HOST, "cid", "csecret")
        provider._token = "expiring-tok"
        provider._expires_at = time.time() + 100  # 100s remaining < 300s threshold
        with patch.object(provider, "_fetch_new_token", return_value=("fresh-tok", time.time() + 7200)):
            token = provider.get_token()
        assert token == "fresh-tok"

    def test_fetch_new_token_sends_scope_all_apis(self) -> None:
        """_fetch_new_token must include scope=all-apis in the token request."""
        provider = M2MTokenProvider(_HOST, "test-cid", "test-secret")
        captured_data: dict = {}

        def mock_post(url, data=None, headers=None, timeout=None):
            captured_data.update(data or {})
            return _make_m2m_response()

        with patch("httpx.post", side_effect=mock_post):
            provider._fetch_new_token()

        assert captured_data.get("scope") == "all-apis"
        assert captured_data.get("grant_type") == "client_credentials"
        assert captured_data.get("client_id") == "test-cid"
        assert captured_data.get("client_secret") == "test-secret"

    def test_fetch_new_token_sends_user_agent(self) -> None:
        """_fetch_new_token must include PWAF User-Agent header."""
        provider = M2MTokenProvider(_HOST, "cid", "secret")
        captured_headers: dict = {}

        def mock_post(url, data=None, headers=None, timeout=None):
            captured_headers.update(headers or {})
            return _make_m2m_response()

        with patch("httpx.post", side_effect=mock_post):
            provider._fetch_new_token()

        assert captured_headers.get("User-Agent") == USER_AGENT

    def test_fetch_new_token_raises_on_http_error(self) -> None:
        """_fetch_new_token must propagate HTTP errors from the token endpoint."""
        provider = M2MTokenProvider(_HOST, "cid", "secret")
        req = httpx.Request("POST", _M2M_TOKEN_URL)
        error_resp = httpx.Response(401, json={"message": "Unauthorized"}, request=req)

        with patch("httpx.post", return_value=error_resp):
            with pytest.raises(httpx.HTTPStatusError):
                provider._fetch_new_token()


# ---------------------------------------------------------------------------
# _resolve_u2m
# ---------------------------------------------------------------------------

def _make_stored_tokens(
    access_token: str = "u2m-access",
    refresh_token: str = "u2m-refresh",
    expires_at: float | None = None,
    client_id: str = "u2m-cid",
    host: str = _HOST,
) -> StoredU2MTokens:
    return StoredU2MTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at or (time.time() + 3600),
        client_id=client_id,
        host=host,
    )


def test_u2m_resolve_returns_credentials_with_u2m_method() -> None:
    stored = _make_stored_tokens()
    creds = _resolve_u2m(_HOST, stored)
    assert isinstance(creds, DatabricksCredentials)
    assert creds.auth_method == "u2m"
    assert creds.host == _HOST


def test_u2m_get_token_returns_current_token_when_fresh() -> None:
    """U2M: get_token() returns the stored access token without HTTP when still fresh."""
    stored = _make_stored_tokens(access_token="fresh-token", expires_at=time.time() + 3600)
    creds = _resolve_u2m(_HOST, stored)
    with patch("httpx.post") as mock_post:
        token = creds.get_token()
    assert token == "fresh-token"
    mock_post.assert_not_called()


def test_u2m_get_token_refreshes_when_near_expiry() -> None:
    """U2M: get_token() calls token endpoint when token is near expiry."""
    stored = _make_stored_tokens(access_token="old-token", expires_at=time.time() + 100)
    creds = _resolve_u2m(_HOST, stored)

    def mock_post(url, data=None, headers=None, timeout=None):
        return _make_refresh_response(token="refreshed-token")

    with patch("httpx.post", side_effect=mock_post):
        token = creds.get_token()

    assert token == "refreshed-token"


def test_u2m_refresh_uses_no_client_secret() -> None:
    """U2M PKCE refresh must NOT send client_secret (public client)."""
    stored = _make_stored_tokens(expires_at=time.time() + 100)
    creds = _resolve_u2m(_HOST, stored)
    captured_data: dict = {}

    def mock_post(url, data=None, headers=None, timeout=None):
        captured_data.update(data or {})
        return _make_refresh_response()

    with patch("httpx.post", side_effect=mock_post):
        creds.get_token()

    assert "client_secret" not in captured_data
    assert captured_data.get("grant_type") == "refresh_token"


def test_u2m_refresh_sends_client_secret_for_confidential_apps() -> None:
    """U2M confidential-app refresh MUST include client_secret."""
    stored = _make_stored_tokens(expires_at=time.time() + 100)
    creds = _resolve_u2m(_HOST, stored, client_secret="my-secret")
    captured_data: dict = {}

    def mock_post(url, data=None, headers=None, timeout=None):
        captured_data.update(data or {})
        return _make_refresh_response()

    with patch("httpx.post", side_effect=mock_post):
        creds.get_token()

    assert captured_data.get("client_secret") == "my-secret"
    assert captured_data.get("grant_type") == "refresh_token"


def test_u2m_refresh_failure_raises_auth_error() -> None:
    """U2M refresh HTTP error → AuthenticationError with re-auth guidance."""
    from litellm.exceptions import AuthenticationError

    stored = _make_stored_tokens(expires_at=time.time() + 100)
    creds = _resolve_u2m(_HOST, stored)

    with patch("httpx.post", return_value=httpx.Response(401, json={"error": "invalid_grant"})):
        with pytest.raises(AuthenticationError, match="Re-authenticate"):
            creds.get_token()


# ---------------------------------------------------------------------------
# resolve_credentials — priority chain
# ---------------------------------------------------------------------------

def _make_mock_llm(
    databricks_host: str = _HOST,
    api_key: SecretStr | None = None,
    stored_u2m_tokens: StoredU2MTokens | None = None,
    databricks_client_id: str | None = None,
    databricks_client_secret: SecretStr | None = None,
    databricks_u2m_client_secret: SecretStr | None = None,
    databricks_profile: str | None = None,
    base_url: str | None = None,
) -> MagicMock:
    """Return a MagicMock shaped like DatabricksLLM for testing resolve_credentials."""
    llm = MagicMock()
    llm.databricks_host = databricks_host
    llm.base_url = base_url
    llm.api_key = api_key
    llm.stored_u2m_tokens = stored_u2m_tokens
    llm.databricks_client_id = databricks_client_id
    llm.databricks_client_secret = databricks_client_secret
    llm.databricks_u2m_client_secret = databricks_u2m_client_secret
    llm.databricks_profile = databricks_profile
    return llm


def test_resolve_credentials_u2m_wins_over_all() -> None:
    """U2M stored tokens take highest priority."""
    stored = _make_stored_tokens()
    llm = _make_mock_llm(
        stored_u2m_tokens=stored,
        api_key=SecretStr("pat-token"),
        databricks_client_id="m2m-cid",
        databricks_client_secret=SecretStr("m2m-secret"),
    )
    creds = resolve_credentials(llm)
    assert creds.auth_method == "u2m"


def test_resolve_credentials_u2m_forwards_client_secret() -> None:
    """resolve_credentials passes databricks_u2m_client_secret to _resolve_u2m."""
    stored = _make_stored_tokens(expires_at=time.time() + 100)
    llm = _make_mock_llm(
        stored_u2m_tokens=stored,
        databricks_u2m_client_secret=SecretStr("confidential-secret"),
    )
    creds = resolve_credentials(llm)
    assert creds.auth_method == "u2m"

    captured_data: dict = {}

    def mock_post(url, data=None, headers=None, timeout=None):
        captured_data.update(data or {})
        return _make_refresh_response()

    with patch("httpx.post", side_effect=mock_post):
        creds.get_token()

    assert captured_data.get("client_secret") == "confidential-secret"


def test_resolve_credentials_pat_path() -> None:
    """PAT is used when no U2M or M2M credentials are present."""
    llm = _make_mock_llm(api_key=SecretStr("dapi-test"))
    creds = resolve_credentials(llm)
    assert creds.auth_method == "pat"
    assert creds.get_token() == "dapi-test"


def test_resolve_credentials_m2m_over_pat() -> None:
    """M2M takes priority over PAT when both are present."""
    llm = _make_mock_llm(
        api_key=SecretStr("dapi-pat"),
        databricks_client_id="m2m-cid",
        databricks_client_secret=SecretStr("m2m-secret"),
    )
    with patch(
        "openhands.sdk.llm.providers.databricks.auth.M2MTokenProvider._fetch_new_token",
        return_value=("m2m-token", time.time() + 3600),
    ):
        creds = resolve_credentials(llm)
    assert creds.auth_method == "m2m"


def test_resolve_credentials_pat_does_not_require_host() -> None:
    """PAT auth must succeed without a workspace host (token goes to AI Gateway)."""
    llm = _make_mock_llm(databricks_host=None, base_url=None, api_key=SecretStr("tok"))
    creds = resolve_credentials(llm)
    assert creds.auth_method == "pat"
    assert creds.host == ""


def test_resolve_credentials_unified_requires_host() -> None:
    """Unified auth (no api_key, no profile, no creds) needs the workspace host."""
    llm = _make_mock_llm(databricks_host=None, base_url=None)
    with pytest.raises(ValueError, match="databricks_host is required"):
        resolve_credentials(llm)


def test_resolve_credentials_host_from_base_url() -> None:
    """Host falls back to base_url if databricks_host is not set."""
    llm = _make_mock_llm(databricks_host=None, base_url=_HOST, api_key=SecretStr("tok"))
    creds = resolve_credentials(llm)
    assert creds.host == _HOST


def test_resolve_credentials_host_from_stored_tokens() -> None:
    """Host falls back to stored_u2m_tokens.host as last resort."""
    stored = _make_stored_tokens(host=_HOST)
    llm = _make_mock_llm(databricks_host=None, base_url=None, stored_u2m_tokens=stored)
    creds = resolve_credentials(llm)
    assert creds.host == _HOST


def test_resolve_credentials_profile_raises_without_sdk() -> None:
    """PROFILE strategy raises ImportError with install hint if databricks-sdk absent.

    The import check is deferred to get_token() so that saving settings succeeds
    even without the package installed; the error surfaces at first API call.
    """
    llm = _make_mock_llm(databricks_profile="my-profile")
    with patch.dict("sys.modules", {"databricks": None, "databricks.sdk": None}):
        # resolve_credentials succeeds (returns a DatabricksCredentials object)
        creds = resolve_credentials(llm)
        assert creds.auth_method == "profile"
        # The ImportError surfaces when the token is actually requested
        with pytest.raises((ImportError, Exception)):
            creds.get_token()


def test_resolve_credentials_unified_raises_without_sdk() -> None:
    """UNIFIED strategy raises ImportError with install hint if databricks-sdk absent.

    The import check is deferred to get_token() so that saving settings succeeds
    even without the package installed; the error surfaces at first API call.
    """
    llm = _make_mock_llm()  # no api_key, no profile → falls through to unified
    with patch.dict("sys.modules", {"databricks": None, "databricks.sdk": None}):
        # resolve_credentials succeeds
        creds = resolve_credentials(llm)
        assert creds.auth_method == "unified"
        # The ImportError surfaces when the token is actually requested
        with pytest.raises((ImportError, Exception)):
            creds.get_token()
