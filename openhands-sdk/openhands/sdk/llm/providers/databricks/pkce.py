"""Databricks U2M OAuth PKCE primitives (shared by web + CLI front-ends).

These are the dependency-light helpers for the interactive *browser login*
(Authorization Code + PKCE) flow:

* :func:`generate_pkce`           — verifier / S256 challenge pair.
* :func:`build_authorize_url`     — Databricks OIDC ``/authorize`` URL.
* :func:`exchange_code_for_tokens`       — sync code → tokens exchange.
* :func:`async_exchange_code_for_tokens` — async variant for event-loop callers.

The provider's :mod:`.auth` module owns token *refresh*; this module owns the
one-time *login*. Both the OpenHands web app and the OpenHands CLI consume these
helpers so the PKCE logic lives in exactly one place.

The returned token dict round-trips through
:class:`~openhands.sdk.llm.providers.databricks.models.StoredU2MTokens`:
``access_token``, ``refresh_token``, ``expires_at``, ``client_id``, ``host``.

No ``litellm`` / FastAPI imports here — kept minimal so both front-ends (which
may pin different framework versions) can import it cheaply.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from openhands.sdk.llm.providers.databricks.utils import USER_AGENT


_TOKEN_TIMEOUT_S = 15.0
_DEFAULT_EXPIRES_IN = 3600


def generate_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` where challenge is S256 of verifier."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(
    host: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
) -> str:
    """Build the Databricks OIDC authorize URL with PKCE (S256)."""
    host = host.rstrip("/")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "all-apis offline_access",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{host}/oidc/v1/authorize?{urlencode(params)}"


def _build_token_request(
    host: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
    client_secret: str | None,
) -> tuple[str, dict[str, str]]:
    """Return ``(token_url, form_data)`` for the code → token exchange.

    ``client_secret`` is required for **confidential** OAuth apps (apps with a
    secret registered in Databricks App connections). Public PKCE apps omit it;
    omitting it for a confidential app returns ``{"error": "invalid_client"}``.
    """
    host = host.rstrip("/")
    token_data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if client_secret:
        token_data["client_secret"] = client_secret
    return f"{host}/oidc/v1/token", token_data


def _to_stored_payload(
    data: dict[str, Any], client_id: str, host: str
) -> dict[str, Any]:
    """Shape a Databricks token response into a ``StoredU2MTokens``-compatible dict."""
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + data.get("expires_in", _DEFAULT_EXPIRES_IN),
        "client_id": client_id,
        "host": host.rstrip("/"),
    }


def exchange_code_for_tokens(
    host: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for tokens (synchronous).

    Sends the PWAF ``User-Agent`` on the token request. Returns a dict
    compatible with ``StoredU2MTokens.model_validate``.

    Raises:
        httpx.HTTPStatusError: if the token endpoint returns a non-2xx status.
    """
    token_url, token_data = _build_token_request(
        host, client_id, redirect_uri, code, verifier, client_secret
    )
    resp = httpx.post(
        token_url,
        data=token_data,
        headers={"User-Agent": USER_AGENT},
        timeout=_TOKEN_TIMEOUT_S,
    )
    resp.raise_for_status()
    return _to_stored_payload(resp.json(), client_id, host)


async def async_exchange_code_for_tokens(
    host: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for tokens (asynchronous).

    Identical to :func:`exchange_code_for_tokens` but uses
    ``httpx.AsyncClient`` so it does not block the event loop when called from
    an ``async`` request handler (e.g. the web app's OAuth callback route).

    Raises:
        httpx.HTTPStatusError: if the token endpoint returns a non-2xx status.
    """
    token_url, token_data = _build_token_request(
        host, client_id, redirect_uri, code, verifier, client_secret
    )
    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_S) as client:
        resp = await client.post(
            token_url,
            data=token_data,
            headers={"User-Agent": USER_AGENT},
        )
    resp.raise_for_status()
    return _to_stored_payload(resp.json(), client_id, host)
