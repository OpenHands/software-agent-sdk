"""Tests for the shared U2M OAuth PKCE primitives.

Covers: PKCE verifier/challenge S256 correctness, authorize-URL parameters,
and the sync + async code → token exchange (happy path, confidential-app
secret, refresh-token default, PWAF User-Agent header, and error propagation).
"""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from openhands.sdk.llm.providers.databricks.models import StoredU2MTokens
from openhands.sdk.llm.providers.databricks.pkce import (
    async_exchange_code_for_tokens,
    build_authorize_url,
    exchange_code_for_tokens,
    generate_pkce,
)
from openhands.sdk.llm.providers.databricks.utils import USER_AGENT


_HOST = "https://adb-123.azuredatabricks.net"
_CLIENT_ID = "oauth-app-client-id"
_REDIRECT = "http://localhost:3000/auth/databricks/callback"


# ---------------------------------------------------------------------------
# generate_pkce
# ---------------------------------------------------------------------------


def test_generate_pkce_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected


def test_generate_pkce_no_base64_padding() -> None:
    verifier, challenge = generate_pkce()
    assert "=" not in verifier
    assert "=" not in challenge


def test_generate_pkce_is_random() -> None:
    assert generate_pkce()[0] != generate_pkce()[0]


# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


def test_build_authorize_url_params() -> None:
    url = build_authorize_url(_HOST, _CLIENT_ID, _REDIRECT, "state123", "chal456")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.path == "/oidc/v1/authorize"
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == [_CLIENT_ID]
    assert qs["redirect_uri"] == [_REDIRECT]
    assert qs["scope"] == ["all-apis offline_access"]
    assert qs["state"] == ["state123"]
    assert qs["code_challenge"] == ["chal456"]
    assert qs["code_challenge_method"] == ["S256"]


def test_build_authorize_url_strips_trailing_slash() -> None:
    url = build_authorize_url(_HOST + "/", _CLIENT_ID, _REDIRECT, "s", "c")
    assert url.startswith(f"{_HOST}/oidc/v1/authorize?")


# ---------------------------------------------------------------------------
# exchange_code_for_tokens (sync)
# ---------------------------------------------------------------------------


def _mock_token_response() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "access_token": "access-abc",
        "refresh_token": "refresh-xyz",
        "expires_in": 3600,
    }
    return resp


def test_exchange_code_returns_stored_token_shape() -> None:
    with patch("httpx.post", return_value=_mock_token_response()) as mock_post:
        payload = exchange_code_for_tokens(
            _HOST, _CLIENT_ID, _REDIRECT, "auth-code", "verifier-1"
        )

    # Round-trips through the StoredU2MTokens model.
    stored = StoredU2MTokens.model_validate(payload)
    assert stored.access_token == "access-abc"
    assert stored.refresh_token == "refresh-xyz"
    assert stored.client_id == _CLIENT_ID
    assert stored.host == _HOST
    assert stored.expires_at > 0

    # Token endpoint + PWAF User-Agent + correct form fields.
    args, kwargs = mock_post.call_args
    assert args[0] == f"{_HOST}/oidc/v1/token"
    assert kwargs["headers"]["User-Agent"] == USER_AGENT
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "auth-code"
    assert kwargs["data"]["code_verifier"] == "verifier-1"
    assert "client_secret" not in kwargs["data"]


def test_exchange_code_includes_client_secret_for_confidential_app() -> None:
    with patch("httpx.post", return_value=_mock_token_response()) as mock_post:
        exchange_code_for_tokens(
            _HOST, _CLIENT_ID, _REDIRECT, "code", "verifier",
            client_secret="super-secret",
        )
    assert mock_post.call_args.kwargs["data"]["client_secret"] == "super-secret"


def test_exchange_code_defaults_missing_refresh_token() -> None:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"access_token": "a", "expires_in": 1200}
    with patch("httpx.post", return_value=resp):
        payload = exchange_code_for_tokens(
            _HOST, _CLIENT_ID, _REDIRECT, "code", "verifier"
        )
    assert payload["refresh_token"] == ""


def test_exchange_code_propagates_http_error() -> None:
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "invalid_client", request=MagicMock(), response=MagicMock()
    )
    with patch("httpx.post", return_value=resp):
        with pytest.raises(httpx.HTTPStatusError):
            exchange_code_for_tokens(_HOST, _CLIENT_ID, _REDIRECT, "code", "verifier")


# ---------------------------------------------------------------------------
# async_exchange_code_for_tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_exchange_code_returns_stored_token_shape() -> None:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "access_token": "access-async",
        "refresh_token": "refresh-async",
        "expires_in": 3600,
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = resp

    with patch("httpx.AsyncClient", return_value=mock_client):
        payload = await async_exchange_code_for_tokens(
            _HOST, _CLIENT_ID, _REDIRECT, "code", "verifier",
            client_secret="conf-secret",
        )

    stored = StoredU2MTokens.model_validate(payload)
    assert stored.access_token == "access-async"
    assert stored.host == _HOST

    # PWAF User-Agent + confidential secret forwarded on the async path too.
    kwargs = mock_client.post.call_args.kwargs
    assert kwargs["headers"]["User-Agent"] == USER_AGENT
    assert kwargs["data"]["client_secret"] == "conf-secret"


@pytest.mark.asyncio
async def test_async_exchange_code_propagates_http_error() -> None:
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=MagicMock()
    )
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = resp

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await async_exchange_code_for_tokens(
                _HOST, _CLIENT_ID, _REDIRECT, "code", "verifier"
            )
