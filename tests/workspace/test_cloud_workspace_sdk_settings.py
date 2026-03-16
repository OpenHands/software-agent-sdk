"""Tests for OpenHandsCloudWorkspace.get_llm() and get_secrets() methods.

get_llm() returns a real LLM with the raw api_key from SaaS.
get_secrets() returns LookupSecret references — raw values only flow
SaaS→sandbox, never to the SDK client.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk.secret import LookupSecret
from openhands.workspace.cloud.workspace import OpenHandsCloudWorkspace


SANDBOX_ID = "sb-test-123"
SESSION_KEY = "session-key-abc"
CLOUD_URL = "https://app.all-hands.dev"


@pytest.fixture
def mock_workspace():
    """Create a workspace instance with mocked sandbox lifecycle."""
    with patch.object(
        OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
    ):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url=CLOUD_URL,
            cloud_api_key="test-api-key",
            host="http://localhost:8000",
        )
    # Simulate a running sandbox
    workspace._sandbox_id = SANDBOX_ID
    workspace._session_api_key = SESSION_KEY
    return workspace


class TestGetLLM:
    """Tests for OpenHandsCloudWorkspace.get_llm()."""

    def test_get_llm_returns_usable_llm(self, mock_workspace):
        """get_llm fetches SaaS config and returns a usable LLM."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "llm_model": "anthropic/claude-sonnet-4-20250514",
            "llm_api_key": "sk-test-key-123",
            "llm_base_url": "https://litellm.example.com",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ) as mock_req:
            llm = mock_workspace.get_llm()

        mock_req.assert_called_once_with(
            "GET",
            f"{CLOUD_URL}/api/v1/users/me",
            params={"expose_secrets": "true"},
            headers={"X-Session-API-Key": SESSION_KEY},
        )
        assert llm.model == "anthropic/claude-sonnet-4-20250514"
        # api_key is a real SecretStr (LLM validator converts str → SecretStr)
        assert isinstance(llm.api_key, SecretStr)
        assert llm.api_key.get_secret_value() == "sk-test-key-123"
        assert llm.base_url == "https://litellm.example.com"

    def test_get_llm_allows_overrides(self, mock_workspace):
        """User-provided kwargs override SaaS settings."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "llm_model": "anthropic/claude-sonnet-4-20250514",
            "llm_api_key": "sk-test-key",
            "llm_base_url": None,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            llm = mock_workspace.get_llm(model="gpt-4o", temperature=0.5)

        assert llm.model == "gpt-4o"
        assert llm.temperature == 0.5
        assert isinstance(llm.api_key, SecretStr)

    def test_get_llm_no_api_key_still_works(self, mock_workspace):
        """If no API key is configured, the LLM gets api_key=None."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "llm_model": "gpt-4o",
            "llm_api_key": None,
            "llm_base_url": None,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            llm = mock_workspace.get_llm()

        assert llm.model == "gpt-4o"
        assert llm.api_key is None

    def test_get_llm_raises_when_no_sandbox(self, mock_workspace):
        """get_llm raises RuntimeError when sandbox is not running."""
        mock_workspace._sandbox_id = None
        with pytest.raises(RuntimeError, match="Sandbox is not running"):
            mock_workspace.get_llm()


class TestGetSecrets:
    """Tests for OpenHandsCloudWorkspace.get_secrets()."""

    def test_get_all_secrets_returns_lookup_secrets(self, mock_workspace):
        """get_secrets returns LookupSecret instances, not raw values."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "secrets": [
                {"name": "GITHUB_TOKEN", "description": "GitHub token"},
                {"name": "MY_API_KEY", "description": None},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_settings_request", return_value=mock_response
        ) as mock_req:
            secrets = mock_workspace.get_secrets()

        mock_req.assert_called_once_with(
            "GET",
            f"{CLOUD_URL}/api/v1/sandboxes/{SANDBOX_ID}/settings/secrets",
        )

        assert len(secrets) == 2
        assert "GITHUB_TOKEN" in secrets
        assert "MY_API_KEY" in secrets

        gh_secret = secrets["GITHUB_TOKEN"]
        assert isinstance(gh_secret, LookupSecret)
        assert gh_secret.url == (
            f"{CLOUD_URL}/api/v1/sandboxes/{SANDBOX_ID}/settings/secrets/GITHUB_TOKEN"
        )
        assert gh_secret.headers == {"X-Session-API-Key": SESSION_KEY}
        assert gh_secret.description == "GitHub token"

    def test_get_secrets_filters_by_name(self, mock_workspace):
        """get_secrets(names=[...]) filters client-side."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "secrets": [
                {"name": "GITHUB_TOKEN", "description": "GitHub token"},
                {"name": "MY_API_KEY", "description": None},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_settings_request", return_value=mock_response
        ):
            secrets = mock_workspace.get_secrets(names=["GITHUB_TOKEN"])

        assert len(secrets) == 1
        assert "GITHUB_TOKEN" in secrets
        assert "MY_API_KEY" not in secrets

    def test_get_secrets_empty(self, mock_workspace):
        """Empty secrets list returns empty dict."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"secrets": []}
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_settings_request", return_value=mock_response
        ):
            secrets = mock_workspace.get_secrets()

        assert secrets == {}

    def test_get_secrets_raises_when_no_sandbox(self, mock_workspace):
        """get_secrets raises RuntimeError when sandbox is not running."""
        mock_workspace._sandbox_id = None
        with pytest.raises(RuntimeError, match="Sandbox is not running"):
            mock_workspace.get_secrets()
