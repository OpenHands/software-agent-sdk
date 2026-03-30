"""Tests for the LLMCapabilities class."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.llm.capabilities import (
    DEFAULT_MAX_OUTPUT_TOKENS_CAP,
    MIN_CONTEXT_WINDOW_TOKENS,
    CapabilitiesConfig,
    LLMCapabilities,
)
from openhands.sdk.llm.exceptions import LLMContextWindowTooSmallError


@pytest.fixture
def mock_model_info():
    """Default mock model info for testing."""
    return {
        "max_input_tokens": 128000,
        "max_output_tokens": 16384,
        "supports_vision": True,
    }


@pytest.fixture
def base_config_kwargs():
    """Base kwargs for creating CapabilitiesConfig."""
    return {
        "model": "claude-sonnet-4-20250514",
        "model_canonical_name": None,
        "base_url": None,
        "api_key": SecretStr("test-key"),
        "disable_vision": False,
        "caching_prompt": True,
    }


def _make_caps(kwargs: dict) -> LLMCapabilities:
    """Helper to create LLMCapabilities from a kwargs dict."""
    return LLMCapabilities(CapabilitiesConfig(**kwargs))


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_capabilities_initialization(
    mock_get_info, mock_model_info, base_config_kwargs
):
    """Test basic initialization of LLMCapabilities."""
    mock_get_info.return_value = mock_model_info
    # Use a model that doesn't have special output token handling
    base_config_kwargs["model"] = "gpt-4o"

    caps = _make_caps(base_config_kwargs)

    assert caps.model == "gpt-4o"
    assert caps.model_name_for_capabilities == "gpt-4o"
    assert caps.detected_max_input_tokens == 128000
    assert caps.detected_max_output_tokens == 16384
    assert caps.model_info == mock_model_info


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_model_canonical_name_override(
    mock_get_info, mock_model_info, base_config_kwargs
):
    """Test that model_canonical_name overrides model for capabilities."""
    mock_get_info.return_value = mock_model_info
    base_config_kwargs["model_canonical_name"] = "anthropic/claude-sonnet-4"

    caps = _make_caps(base_config_kwargs)

    assert caps.model == "claude-sonnet-4-20250514"
    assert caps.model_name_for_capabilities == "anthropic/claude-sonnet-4"


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_max_output_tokens_cap_from_max_tokens(mock_get_info, base_config_kwargs):
    """Test that max_tokens is capped to DEFAULT_MAX_OUTPUT_TOKENS_CAP."""
    mock_get_info.return_value = {
        "max_input_tokens": 200000,
        "max_tokens": 200000,  # Ambiguous - could be context window
    }
    # Use a model that doesn't have special output token handling
    base_config_kwargs["model"] = "gpt-4o"

    caps = _make_caps(base_config_kwargs)

    # Should be capped to avoid exceeding context window
    assert caps.detected_max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS_CAP


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_claude_extended_output_tokens(mock_get_info, base_config_kwargs):
    """Test that Claude models get extended max_output_tokens."""
    mock_get_info.return_value = {"max_input_tokens": 200000}
    base_config_kwargs["model"] = "claude-sonnet-4-20250514"

    caps = _make_caps(base_config_kwargs)

    assert caps.detected_max_output_tokens == 64000


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_o3_output_tokens_clamped(mock_get_info, base_config_kwargs):
    """Test that o3 models have output tokens clamped to 100k."""
    mock_get_info.return_value = {
        "max_input_tokens": 200000,
        "max_output_tokens": 200000,
    }
    base_config_kwargs["model"] = "o3-2025-04-16"

    caps = _make_caps(base_config_kwargs)

    assert caps.detected_max_output_tokens == 100000


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
@patch("openhands.sdk.llm.capabilities.supports_vision", return_value=True)
def test_vision_is_active_when_supported(mock_sv, mock_get_info, base_config_kwargs):
    """Test vision_is_active returns True when model supports vision."""
    mock_get_info.return_value = {"supports_vision": True}
    base_config_kwargs["disable_vision"] = False

    caps = _make_caps(base_config_kwargs)

    assert caps.vision_is_active() is True


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
@patch("openhands.sdk.llm.capabilities.supports_vision", return_value=True)
def test_vision_is_active_when_disabled(mock_sv, mock_get_info, base_config_kwargs):
    """Test vision_is_active returns False when disable_vision=True."""
    mock_get_info.return_value = {"supports_vision": True}
    base_config_kwargs["disable_vision"] = True

    caps = _make_caps(base_config_kwargs)

    assert caps.vision_is_active() is False


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
@patch("openhands.sdk.llm.capabilities.supports_vision", return_value=False)
def test_vision_is_active_when_not_supported(
    mock_sv, mock_get_info, base_config_kwargs
):
    """Test vision_is_active returns False when model doesn't support vision."""
    mock_get_info.return_value = {"supports_vision": False}
    base_config_kwargs["disable_vision"] = False

    caps = _make_caps(base_config_kwargs)

    assert caps.vision_is_active() is False


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_caching_prompt_active_for_claude(mock_get_info, base_config_kwargs):
    """Test that caching is active for Claude models when enabled."""
    mock_get_info.return_value = {}
    base_config_kwargs["model"] = "claude-3-5-sonnet"
    base_config_kwargs["caching_prompt"] = True

    caps = _make_caps(base_config_kwargs)

    assert caps.is_caching_prompt_active() is True


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_caching_prompt_inactive_when_disabled(mock_get_info, base_config_kwargs):
    """Test that caching is inactive when caching_prompt=False."""
    mock_get_info.return_value = {}
    base_config_kwargs["model"] = "claude-3-5-sonnet"
    base_config_kwargs["caching_prompt"] = False

    caps = _make_caps(base_config_kwargs)

    assert caps.is_caching_prompt_active() is False


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_caching_prompt_inactive_for_unsupported_model(
    mock_get_info, base_config_kwargs
):
    """Test that caching is inactive for models that don't support it."""
    mock_get_info.return_value = {}
    base_config_kwargs["model"] = "gpt-4o"
    base_config_kwargs["caching_prompt"] = True

    caps = _make_caps(base_config_kwargs)

    assert caps.is_caching_prompt_active() is False


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_uses_responses_api_for_gpt5(mock_get_info, base_config_kwargs):
    """Test that GPT-5 models use the Responses API."""
    mock_get_info.return_value = {}
    base_config_kwargs["model"] = "gpt-5.2"

    caps = _make_caps(base_config_kwargs)

    assert caps.uses_responses_api() is True


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_uses_responses_api_false_for_older_models(mock_get_info, base_config_kwargs):
    """Test that older models don't use the Responses API."""
    mock_get_info.return_value = {}
    base_config_kwargs["model"] = "gpt-4o"

    caps = _make_caps(base_config_kwargs)

    assert caps.uses_responses_api() is False


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_context_window_too_small_raises_error(mock_get_info, base_config_kwargs):
    """Test that small context windows raise LLMContextWindowTooSmallError."""
    mock_get_info.return_value = {"max_input_tokens": 4096}

    with pytest.raises(LLMContextWindowTooSmallError) as exc_info:
        _make_caps(base_config_kwargs)

    # Check the error message contains expected values
    assert "4,096" in str(exc_info.value)
    assert str(MIN_CONTEXT_WINDOW_TOKENS) in str(exc_info.value).replace(",", "")


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
@patch.dict("os.environ", {"ALLOW_SHORT_CONTEXT_WINDOWS": "true"})
def test_context_window_check_can_be_bypassed(mock_get_info, base_config_kwargs):
    """Test that context window check can be bypassed with env var."""
    mock_get_info.return_value = {"max_input_tokens": 4096}

    # Should not raise
    caps = _make_caps(base_config_kwargs)

    assert caps.detected_max_input_tokens == 4096


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_unknown_context_window_passes_validation(mock_get_info, base_config_kwargs):
    """Test that unknown context window (None) doesn't fail validation."""
    mock_get_info.return_value = {}  # No max_input_tokens

    # Should not raise
    caps = _make_caps(base_config_kwargs)

    assert caps.detected_max_input_tokens is None


@patch("openhands.sdk.llm.capabilities.get_litellm_model_info")
def test_model_info_returns_cached_info(
    mock_get_info, mock_model_info, base_config_kwargs
):
    """Test that model_info property returns the cached model info."""
    mock_get_info.return_value = mock_model_info

    caps = _make_caps(base_config_kwargs)

    assert caps.model_info is mock_model_info
    # Verify get_litellm_model_info was only called once
    mock_get_info.assert_called_once()
