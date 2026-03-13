"""Tests for OpenHandsCloudWorkspace.get_llm() and get_secrets() methods."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.workspace.cloud.workspace import OpenHandsCloudWorkspace


@pytest.fixture
def mock_workspace():
    """Create a workspace instance with mocked sandbox lifecycle."""
    with patch.object(OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="test-api-key",
            host="http://localhost:8000",
        )
    return workspace


class TestGetLLM:
    """Tests for OpenHandsCloudWorkspace.get_llm()."""

    def test_get_llm_returns_configured_instance(self, mock_workspace):
        """Test that get_llm returns an LLM with SaaS settings."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model": "anthropic/claude-sonnet-4-20250514",
            "api_key": "sk-test-key-123",
            "base_url": "https://litellm.example.com",
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ) as mock_request:
            llm = mock_workspace.get_llm()

        mock_request.assert_called_once_with(
            "GET",
            "https://app.all-hands.dev/api/v1/users/settings/llm",
        )
        assert llm.model == "anthropic/claude-sonnet-4-20250514"
        assert llm.api_key == SecretStr("sk-test-key-123")
        assert llm.base_url == "https://litellm.example.com"

    def test_get_llm_allows_overrides(self, mock_workspace):
        """Test that user-provided kwargs override SaaS settings."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model": "anthropic/claude-sonnet-4-20250514",
            "api_key": "sk-saas-key",
            "base_url": "https://litellm.example.com",
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            llm = mock_workspace.get_llm(model="gpt-4o", temperature=0.5)

        assert llm.model == "gpt-4o"
        assert llm.temperature == 0.5
        # api_key and base_url should come from SaaS
        assert llm.api_key == SecretStr("sk-saas-key")
        assert llm.base_url == "https://litellm.example.com"

    def test_get_llm_raises_when_no_api_key(self, mock_workspace):
        """Test that get_llm raises ValueError when no API key is available."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model": "gpt-4o",
            "api_key": None,
            "base_url": None,
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            with pytest.raises(ValueError, match="No LLM API key"):
                mock_workspace.get_llm()

    def test_get_llm_with_partial_response(self, mock_workspace):
        """Test get_llm with only model and key (no base_url)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model": "gpt-4o",
            "api_key": "sk-test",
            "base_url": None,
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            llm = mock_workspace.get_llm()

        assert llm.model == "gpt-4o"
        assert llm.api_key == SecretStr("sk-test")
        assert llm.base_url is None


class TestGetSecrets:
    """Tests for OpenHandsCloudWorkspace.get_secrets()."""

    def test_get_all_secrets(self, mock_workspace):
        """Test retrieving all secrets."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "secrets": [
                {
                    "name": "GITHUB_TOKEN",
                    "value": "ghp_test123",
                    "description": "GitHub token",
                },
                {
                    "name": "MY_API_KEY",
                    "value": "my-key",
                    "description": None,
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ) as mock_request:
            secrets = mock_workspace.get_secrets()

        mock_request.assert_called_once_with(
            "GET",
            "https://app.all-hands.dev/api/v1/users/settings/secrets",
            params={},
        )
        assert secrets == {
            "GITHUB_TOKEN": "ghp_test123",
            "MY_API_KEY": "my-key",
        }

    def test_get_secrets_by_name(self, mock_workspace):
        """Test retrieving specific secrets by name."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "secrets": [
                {
                    "name": "GITHUB_TOKEN",
                    "value": "ghp_test123",
                    "description": "GitHub token",
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ) as mock_request:
            secrets = mock_workspace.get_secrets(names=["GITHUB_TOKEN"])

        mock_request.assert_called_once_with(
            "GET",
            "https://app.all-hands.dev/api/v1/users/settings/secrets",
            params={"names": ["GITHUB_TOKEN"]},
        )
        assert secrets == {"GITHUB_TOKEN": "ghp_test123"}

    def test_get_secrets_empty(self, mock_workspace):
        """Test empty secrets response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"secrets": []}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            secrets = mock_workspace.get_secrets()

        assert secrets == {}
