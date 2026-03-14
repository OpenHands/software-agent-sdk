"""Tests for OpenAI subscription authentication.

Note: Tests for JWT verification and JWKS caching have been removed as they
require real OAuth tokens to be meaningful. See GitHub issue #1806 for tracking
integration test requirements.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from openhands.sdk.llm.auth.credentials import CredentialStore, OAuthCredentials
from openhands.sdk.llm.auth.openai import (
    CLIENT_ID,
    CONSENT_BANNER,
    ISSUER,
    OPENAI_CODEX_MODELS,
    OpenAISubscriptionAuth,
    _build_authorize_url,
    _display_consent_and_confirm,
    _generate_pkce,
    _get_consent_marker_path,
    _has_acknowledged_consent,
    _mark_consent_acknowledged,
)


def test_generate_pkce():
    """Test PKCE code generation using authlib."""
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
    assert "gpt-5.3-codex" in OPENAI_CODEX_MODELS
    assert "gpt-5.2-codex" in OPENAI_CODEX_MODELS
    assert "gpt-5.2" in OPENAI_CODEX_MODELS
    assert "gpt-5.1-codex-max" in OPENAI_CODEX_MODELS
    assert "gpt-5.1-codex-mini" in OPENAI_CODEX_MODELS


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
        auth.create_llm(model="gpt-5.2-codex")


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

    llm = auth.create_llm(model="gpt-5.2-codex")

    assert llm.model == "openai/gpt-5.2-codex"
    assert llm.api_key is not None
    assert llm.extra_headers is not None
    # Uses codex_cli_rs to match official Codex CLI for compatibility
    assert llm.extra_headers.get("originator") == "codex_cli_rs"


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
