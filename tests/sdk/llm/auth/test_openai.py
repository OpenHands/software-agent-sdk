"""Tests for OpenAI subscription authentication.

Note: Tests for JWT verification and JWKS caching have been removed as they
require real OAuth tokens to be meaningful. See GitHub issue #1806 for tracking
integration test requirements.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import KeySet, RSAKey

from openhands.sdk.llm.auth.credentials import CredentialStore, OAuthCredentials
from openhands.sdk.llm.auth.openai import (
    CLIENT_ID,
    CONSENT_BANNER,
    ISSUER,
    OPENAI_CODEX_MODELS,
    DeviceCode,
    OpenAISubscriptionAuth,
    _build_authorize_url,
    _display_consent_and_confirm,
    _extract_chatgpt_account_id,
    _generate_pkce,
    _get_consent_marker_path,
    _has_acknowledged_consent,
    _mark_consent_acknowledged,
    _poll_device_code,
    _request_device_code,
)


def test_generate_pkce():
    """Test PKCE code generation."""
    verifier, challenge = _generate_pkce()
    assert verifier is not None
    assert challenge is not None
    assert len(verifier) > 0
    assert len(challenge) > 0
    # Verifier and challenge should be different
    assert verifier != challenge


def test_pkce_codes_are_unique():
    """Test that PKCE codes are unique each time."""
    verifier1, challenge1 = _generate_pkce()
    verifier2, challenge2 = _generate_pkce()
    assert verifier1 != verifier2
    assert challenge1 != challenge2


def test_build_authorize_url():
    """Test building the OAuth authorization URL."""
    code_challenge = "test_challenge"
    state = "test_state"
    redirect_uri = "http://localhost:1455/auth/callback"

    url = _build_authorize_url(redirect_uri, code_challenge, state)

    assert url.startswith(f"{ISSUER}/oauth/authorize?")
    assert f"client_id={CLIENT_ID}" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" in url
    assert "code_challenge=test_challenge" in url
    assert "code_challenge_method=S256" in url
    assert "state=test_state" in url
    assert "originator=openhands" in url
    assert "response_type=code" in url


def test_openai_codex_models():
    """Test that OPENAI_CODEX_MODELS contains expected models."""
    from openhands.sdk.settings.acp_providers import get_acp_provider

    codex_provider = get_acp_provider("codex")
    assert codex_provider is not None
    assert OPENAI_CODEX_MODELS.issuperset(
        model.id for model in codex_provider.available_models
    )
    assert "gpt-5.6" in OPENAI_CODEX_MODELS
    assert "gpt-5.6-sol" in OPENAI_CODEX_MODELS
    assert "gpt-5.6-terra" in OPENAI_CODEX_MODELS
    assert "gpt-5.6-luna" in OPENAI_CODEX_MODELS
    assert "gpt-5.5" in OPENAI_CODEX_MODELS
    assert "gpt-5.4" in OPENAI_CODEX_MODELS
    assert "gpt-5.4-mini" in OPENAI_CODEX_MODELS
    assert "gpt-5.3-codex" not in OPENAI_CODEX_MODELS


def test_openai_subscription_auth_vendor():
    """Test OpenAISubscriptionAuth vendor property."""
    auth = OpenAISubscriptionAuth()
    assert auth.vendor == "openai"


def test_openai_subscription_auth_get_credentials(tmp_path):
    """Test getting credentials from store."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    # No credentials initially
    assert auth.get_credentials() is None

    # Save credentials
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test_access",
        refresh_token="test_refresh",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(creds)

    # Now should return credentials
    retrieved = auth.get_credentials()
    assert retrieved is not None
    assert retrieved.access_token == "test_access"


def test_openai_subscription_auth_has_valid_credentials(tmp_path):
    """Test checking for valid credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    # No credentials
    assert not auth.has_valid_credentials()

    # Valid credentials
    valid_creds = OAuthCredentials(
        vendor="openai",
        access_token="test",
        refresh_token="test",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(valid_creds)
    assert auth.has_valid_credentials()

    # Expired credentials
    expired_creds = OAuthCredentials(
        vendor="openai",
        access_token="test",
        refresh_token="test",
        expires_at=int(time.time() * 1000) - 3600_000,
    )
    store.save(expired_creds)
    assert not auth.has_valid_credentials()


def test_openai_subscription_auth_logout(tmp_path):
    """Test logout removes credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    # Save credentials
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test",
        refresh_token="test",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(creds)
    assert auth.has_valid_credentials()

    # Logout
    assert auth.logout() is True
    assert not auth.has_valid_credentials()

    # Logout again should return False
    assert auth.logout() is False


def test_openai_subscription_auth_create_llm_invalid_model(tmp_path):
    """Test create_llm raises error for invalid model."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    # Save valid credentials
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test",
        refresh_token="test",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(creds)

    with pytest.raises(ValueError, match="not supported for subscription access"):
        auth.create_llm(model="gpt-4o-mini")


def test_openai_subscription_auth_create_llm_no_credentials(tmp_path):
    """Test create_llm raises error when no credentials available."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    with pytest.raises(ValueError, match="No credentials available"):
        auth.create_llm(model="gpt-5.6")


def test_openai_subscription_auth_create_llm_success(tmp_path):
    """Test create_llm creates LLM with correct configuration."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    # Save valid credentials
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test_access_token",
        refresh_token="test_refresh",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(creds)

    llm = auth.create_llm(model="gpt-5.6")

    assert llm.model == "openai/gpt-5.6"
    assert llm.api_key is None
    assert llm._get_litellm_api_key_value() == "test_access_token"
    assert llm.auth_type == "subscription"
    assert llm.subscription_vendor == "openai"
    assert llm.extra_headers is not None
    # Uses codex_cli_rs to match official Codex CLI for compatibility
    assert llm.extra_headers.get("originator") == "codex_cli_rs"


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(status_code=200, payload=None):
    return SimpleNamespace(
        status_code=status_code,
        is_success=200 <= status_code < 300,
        json=lambda: payload or {},
    )


@pytest.mark.asyncio
async def test_request_device_code_success():
    """Test requesting an OpenAI device code."""
    fake_client = _FakeAsyncClient(
        [
            _response(
                payload={
                    "device_auth_id": "device-auth-123",
                    "user_code": "ABCD-1234",
                    "interval": "2",
                }
            )
        ]
    )

    with patch("openhands.sdk.llm.auth.openai.AsyncClient", return_value=fake_client):
        device_code = await _request_device_code()

    assert device_code == DeviceCode(
        verification_url=f"{ISSUER}/codex/device",
        user_code="ABCD-1234",
        device_auth_id="device-auth-123",
        interval=2,
    )
    assert fake_client.posts == [
        (
            f"{ISSUER}/api/accounts/deviceauth/usercode",
            {
                "json": {"client_id": CLIENT_ID},
                "headers": {"Content-Type": "application/json"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_poll_device_code_retries_pending_then_succeeds():
    """Test polling the OpenAI device auth token endpoint."""
    fake_client = _FakeAsyncClient(
        [
            _response(status_code=403),
            _response(
                payload={
                    "authorization_code": "auth-code",
                    "code_verifier": "verifier",
                    "code_challenge": "challenge",
                }
            ),
        ]
    )
    device_code = DeviceCode(
        verification_url=f"{ISSUER}/codex/device",
        user_code="ABCD-1234",
        device_auth_id="device-auth-123",
        interval=1,
    )

    with (
        patch("openhands.sdk.llm.auth.openai.AsyncClient", return_value=fake_client),
        patch("openhands.sdk.llm.auth.openai.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await _poll_device_code(device_code)

    assert result["authorization_code"] == "auth-code"
    assert fake_client.posts == [
        (
            f"{ISSUER}/api/accounts/deviceauth/token",
            {
                "json": {
                    "device_auth_id": "device-auth-123",
                    "user_code": "ABCD-1234",
                },
                "headers": {"Content-Type": "application/json"},
            },
        ),
        (
            f"{ISSUER}/api/accounts/deviceauth/token",
            {
                "json": {
                    "device_auth_id": "device-auth-123",
                    "user_code": "ABCD-1234",
                },
                "headers": {"Content-Type": "application/json"},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_openai_subscription_auth_login_device_code(tmp_path):
    """Test device-code login stores OAuth credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)
    device_code = DeviceCode(
        verification_url=f"{ISSUER}/codex/device",
        user_code="ABCD-1234",
        device_auth_id="device-auth-123",
        interval=1,
    )

    with (
        patch(
            "openhands.sdk.llm.auth.openai._request_device_code",
            new_callable=AsyncMock,
        ) as mock_request,
        patch(
            "openhands.sdk.llm.auth.openai._poll_device_code",
            new_callable=AsyncMock,
        ) as mock_poll,
        patch(
            "openhands.sdk.llm.auth.openai._exchange_code_for_tokens",
            new_callable=AsyncMock,
        ) as mock_exchange,
    ):
        mock_request.return_value = device_code
        mock_poll.return_value = {
            "authorization_code": "auth-code",
            "code_verifier": "verifier",
            "code_challenge": "challenge",
        }
        mock_exchange.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
        }

        credentials = await auth.login(auth_method="device_code")

    assert credentials.access_token == "access"
    assert store.get("openai") is not None
    mock_exchange.assert_called_once_with(
        "auth-code",
        f"{ISSUER}/deviceauth/callback",
        "verifier",
    )


@pytest.mark.asyncio
async def test_openai_subscription_auth_refresh_if_needed_no_creds(tmp_path):
    """Test refresh_if_needed returns None when no credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    result = await auth.refresh_if_needed()
    assert result is None


@pytest.mark.asyncio
async def test_openai_subscription_auth_refresh_if_needed_valid_creds(tmp_path):
    """Test refresh_if_needed returns existing creds when not expired."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    # Save valid credentials
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test_access",
        refresh_token="test_refresh",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(creds)

    result = await auth.refresh_if_needed()
    assert result is not None
    assert result.access_token == "test_access"


@pytest.mark.asyncio
async def test_openai_subscription_auth_refresh_if_needed_expired_creds(tmp_path):
    """Test refresh_if_needed refreshes expired credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    auth = OpenAISubscriptionAuth(credential_store=store)

    # Save expired credentials
    creds = OAuthCredentials(
        vendor="openai",
        access_token="old_access",
        refresh_token="test_refresh",
        expires_at=int(time.time() * 1000) - 3600_000,
    )
    store.save(creds)

    # Mock the refresh function
    with patch(
        "openhands.sdk.llm.auth.openai._refresh_access_token",
        new_callable=AsyncMock,
    ) as mock_refresh:
        mock_refresh.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 3600,
        }

        result = await auth.refresh_if_needed()

        assert result is not None
        assert result.access_token == "new_access"
        mock_refresh.assert_called_once_with("test_refresh")


# =========================================================================
# Tests for consent banner system
# =========================================================================


class TestConsentBannerSystem:
    """Tests for the consent banner and acknowledgment system."""

    def test_consent_banner_content(self):
        """Test that consent banner contains required text."""
        assert "ChatGPT" in CONSENT_BANNER
        assert "Terms of Use" in CONSENT_BANNER
        assert "openai.com/policies/terms-of-use" in CONSENT_BANNER

    def test_consent_marker_path(self, tmp_path):
        """Test that consent marker path is in credentials directory."""
        with patch(
            "openhands.sdk.llm.auth.openai.get_credentials_dir", return_value=tmp_path
        ):
            marker_path = _get_consent_marker_path()
            assert marker_path.parent == tmp_path
            assert ".chatgpt_consent_acknowledged" in str(marker_path)

    def test_has_acknowledged_consent_false_initially(self, tmp_path):
        """Test that consent is not acknowledged initially."""
        with patch(
            "openhands.sdk.llm.auth.openai.get_credentials_dir", return_value=tmp_path
        ):
            assert not _has_acknowledged_consent()

    def test_mark_consent_acknowledged(self, tmp_path):
        """Test marking consent as acknowledged."""
        with patch(
            "openhands.sdk.llm.auth.openai.get_credentials_dir", return_value=tmp_path
        ):
            assert not _has_acknowledged_consent()
            _mark_consent_acknowledged()
            assert _has_acknowledged_consent()

    def test_display_consent_user_accepts(self, tmp_path, capsys):
        """Test consent display when user accepts."""
        with (
            patch(
                "openhands.sdk.llm.auth.openai.get_credentials_dir",
                return_value=tmp_path,
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            result = _display_consent_and_confirm()
            assert result is True

            # Check banner was printed
            captured = capsys.readouterr()
            assert "ChatGPT" in captured.out
            assert "Terms of Use" in captured.out

    def test_display_consent_user_declines(self, tmp_path, capsys):
        """Test consent display when user declines."""
        with (
            patch(
                "openhands.sdk.llm.auth.openai.get_credentials_dir",
                return_value=tmp_path,
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            result = _display_consent_and_confirm()
            assert result is False

    def test_display_consent_non_interactive_first_time_raises(self, tmp_path):
        """Test that non-interactive mode raises error on first time."""
        with (
            patch(
                "openhands.sdk.llm.auth.openai.get_credentials_dir",
                return_value=tmp_path,
            ),
            patch("sys.stdin.isatty", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="non-interactive mode"):
                _display_consent_and_confirm()

    def test_display_consent_non_interactive_after_acknowledgment(self, tmp_path):
        """Test that non-interactive mode works after prior acknowledgment."""
        with patch(
            "openhands.sdk.llm.auth.openai.get_credentials_dir", return_value=tmp_path
        ):
            # Mark consent as acknowledged
            _mark_consent_acknowledged()

            with patch("sys.stdin.isatty", return_value=False):
                result = _display_consent_and_confirm()
                assert result is True

    def test_display_consent_keyboard_interrupt(self, tmp_path):
        """Test handling of keyboard interrupt during consent."""
        with (
            patch(
                "openhands.sdk.llm.auth.openai.get_credentials_dir",
                return_value=tmp_path,
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=KeyboardInterrupt),
        ):
            result = _display_consent_and_confirm()
            assert result is False

    def test_display_consent_eof_error(self, tmp_path):
        """Test handling of EOF during consent."""
        with (
            patch(
                "openhands.sdk.llm.auth.openai.get_credentials_dir",
                return_value=tmp_path,
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=EOFError),
        ):
            result = _display_consent_and_confirm()
            assert result is False


# =========================================================================
# Tests for joserfc migration (no authlib.jose deprecation warning)
# =========================================================================


def test_no_authlib_jose_import():
    """Verify that the openai auth module does not import from authlib.jose.

    The authlib.jose module is deprecated and should be replaced by joserfc.
    """
    import importlib
    import sys

    # Remove cached module to force re-import
    mod_name = "openhands.sdk.llm.auth.openai"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])

    import inspect

    from openhands.sdk.llm.auth import openai as openai_auth_mod

    source = inspect.getsource(openai_auth_mod)
    assert "from authlib.jose" not in source, (
        "Module still imports from the deprecated authlib.jose; use joserfc instead"
    )


def test_joserfc_keyset_import():
    """Test that joserfc KeySet can import a JWKS structure."""
    from joserfc.jwk import KeySetSerialization

    # Minimal valid RSA JWK for testing (RFC 7517 example modulus)
    rsa_n = (
        "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4"
        "cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiF"
        "V4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6C"
        "f0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9"
        "c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWh"
        "AI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1j"
        "F44-csFCur-kEgU8awapJzKnqDKgw"
    )
    test_jwks: KeySetSerialization = {
        "keys": [
            {"kty": "RSA", "kid": "test-key-1", "use": "sig", "n": rsa_n, "e": "AQAB"}
        ]
    }

    key_set = KeySet.import_key_set(test_jwks)
    assert key_set is not None
    # Should have imported one key
    keys = list(key_set)
    assert len(keys) == 1


# =========================================================================
# End-to-end tests for _extract_chatgpt_account_id with joserfc
# =========================================================================


@pytest.fixture
def rsa_signing_key():
    """Generate an RSA key pair for JWT signing in tests."""
    return RSAKey.generate_key(2048, parameters={"kid": "test-key-1"})


@pytest.fixture
def mock_jwks_cache(rsa_signing_key):
    """Mock _jwks_cache to return a KeySet with the test public key."""
    pub_dict = rsa_signing_key.as_dict(private=False)
    key_set = KeySet.import_key_set({"keys": [pub_dict]})
    with patch(
        "openhands.sdk.llm.auth.openai._jwks_cache.get_key_set",
        return_value=key_set,
    ):
        yield


def _sign_jwt(key: RSAKey, claims: dict) -> str:
    """Sign a JWT with the given RSA key and claims."""
    header = {"alg": "RS256", "kid": key.kid}
    return joserfc_jwt.encode(header, claims, key)


def test_extract_chatgpt_account_id_success(rsa_signing_key, mock_jwks_cache):
    """End-to-end: sign a JWT with joserfc, extract chatgpt_account_id."""
    token = _sign_jwt(
        rsa_signing_key,
        {
            "sub": "user-123",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-abc-456",
            },
        },
    )
    account_id = _extract_chatgpt_account_id(token)
    assert account_id == "acct-abc-456"


def test_extract_chatgpt_account_id_missing_claim(rsa_signing_key, mock_jwks_cache):
    """Returns None when the JWT has no chatgpt_account_id claim."""
    token = _sign_jwt(rsa_signing_key, {"sub": "user-123"})
    assert _extract_chatgpt_account_id(token) is None


def test_extract_chatgpt_account_id_wrong_key(rsa_signing_key):
    """Returns None when JWT signature cannot be verified (wrong key)."""
    # Sign with the test key but verify against a different key
    different_key = RSAKey.generate_key(2048, parameters={"kid": "other-key"})
    different_pub = different_key.as_dict(private=False)
    wrong_key_set = KeySet.import_key_set({"keys": [different_pub]})

    token = _sign_jwt(
        rsa_signing_key,
        {
            "sub": "user-123",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-should-not-appear",
            },
        },
    )

    with patch(
        "openhands.sdk.llm.auth.openai._jwks_cache.get_key_set",
        return_value=wrong_key_set,
    ):
        assert _extract_chatgpt_account_id(token) is None


def test_extract_chatgpt_account_id_jwks_fetch_failure():
    """Returns None when JWKS cache raises RuntimeError."""
    with patch(
        "openhands.sdk.llm.auth.openai._jwks_cache.get_key_set",
        side_effect=RuntimeError("network error"),
    ):
        assert _extract_chatgpt_account_id("dummy.jwt.token") is None
