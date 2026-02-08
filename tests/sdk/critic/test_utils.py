"""Tests for critic utility functions."""

from unittest.mock import MagicMock

from pydantic import SecretStr

from openhands.sdk.critic import get_default_critic
from openhands.sdk.critic.impl.api import APIBasedCritic


class TestGetDefaultCritic:
    """Tests for get_default_critic function."""

    def test_returns_none_when_base_url_is_none(self):
        """Test returns None when LLM has no base_url."""
        mock_llm = MagicMock()
        mock_llm.base_url = None
        mock_llm.api_key = "test-key"

        result = get_default_critic(mock_llm)
        assert result is None

    def test_returns_none_when_api_key_is_none(self):
        """Test returns None when LLM has no api_key."""
        mock_llm = MagicMock()
        mock_llm.base_url = "https://llm-proxy.eval.all-hands.dev"
        mock_llm.api_key = None

        result = get_default_critic(mock_llm)
        assert result is None

    def test_returns_none_for_non_allhands_url(self):
        """Test returns None for non-All-Hands URLs."""
        mock_llm = MagicMock()
        mock_llm.api_key = "test-key"

        # Various non-matching URLs
        non_matching_urls = [
            "https://api.openai.com/v1",
            "https://api.anthropic.com",
            "https://example.com",
            "https://llm-proxy.example.com",
            "https://all-hands.dev",
            "https://llm-proxy.all-hands.dev",  # Missing env segment
        ]

        for url in non_matching_urls:
            mock_llm.base_url = url
            result = get_default_critic(mock_llm)
            assert result is None, f"Expected None for URL: {url}"

    def test_returns_critic_for_eval_allhands_url(self):
        """Test returns APIBasedCritic for eval.all-hands.dev."""
        mock_llm = MagicMock()
        mock_llm.base_url = "https://llm-proxy.eval.all-hands.dev"
        mock_llm.api_key = "test-api-key"

        result = get_default_critic(mock_llm)

        assert result is not None
        assert isinstance(result, APIBasedCritic)
        critic = result  # Type narrowing
        assert critic.server_url == "https://llm-proxy.eval.all-hands.dev/vllm"
        # api_key is a SecretStr after validation
        assert isinstance(critic.api_key, SecretStr)
        assert critic.api_key.get_secret_value() == "test-api-key"
        assert critic.model_name == "critic"

    def test_returns_critic_for_staging_allhands_url(self):
        """Test returns APIBasedCritic for staging.all-hands.dev."""
        mock_llm = MagicMock()
        mock_llm.base_url = "https://llm-proxy.staging.all-hands.dev"
        mock_llm.api_key = "staging-key"

        result = get_default_critic(mock_llm)

        assert result is not None
        assert isinstance(result, APIBasedCritic)
        critic = result  # Type narrowing
        assert critic.server_url == "https://llm-proxy.staging.all-hands.dev/vllm"
        assert isinstance(critic.api_key, SecretStr)
        assert critic.api_key.get_secret_value() == "staging-key"
        assert critic.model_name == "critic"

    def test_returns_critic_for_prod_allhands_url(self):
        """Test returns APIBasedCritic for prod.all-hands.dev."""
        mock_llm = MagicMock()
        mock_llm.base_url = "https://llm-proxy.prod.all-hands.dev"
        mock_llm.api_key = "prod-key"

        result = get_default_critic(mock_llm)

        assert result is not None
        assert isinstance(result, APIBasedCritic)
        critic = result  # Type narrowing
        assert critic.server_url == "https://llm-proxy.prod.all-hands.dev/vllm"
        assert isinstance(critic.api_key, SecretStr)
        assert critic.api_key.get_secret_value() == "prod-key"

    def test_handles_trailing_slash_in_base_url(self):
        """Test handles trailing slash in base_url."""
        mock_llm = MagicMock()
        mock_llm.base_url = "https://llm-proxy.eval.all-hands.dev/"
        mock_llm.api_key = "test-key"

        result = get_default_critic(mock_llm)

        assert result is not None
        assert isinstance(result, APIBasedCritic)
        critic = result  # Type narrowing
        # Should not have double slash
        assert critic.server_url == "https://llm-proxy.eval.all-hands.dev/vllm"

    def test_handles_http_url(self):
        """Test handles http:// URL (not just https://)."""
        mock_llm = MagicMock()
        mock_llm.base_url = "http://llm-proxy.local.all-hands.dev"
        mock_llm.api_key = "local-key"

        result = get_default_critic(mock_llm)

        assert result is not None
        assert isinstance(result, APIBasedCritic)
        assert result.server_url == "http://llm-proxy.local.all-hands.dev/vllm"
