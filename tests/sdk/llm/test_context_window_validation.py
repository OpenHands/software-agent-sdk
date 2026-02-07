"""Tests for context window size validation in LLM initialization."""

import os
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import LLMContextWindowTooSmallError
from openhands.sdk.llm.llm import (
    ENV_ALLOW_SHORT_CONTEXT_WINDOWS,
    MIN_CONTEXT_WINDOW_TOKENS,
)


def test_llm_context_window_too_small_error_default_message():
    """Test LLMContextWindowTooSmallError with default message."""
    error = LLMContextWindowTooSmallError(context_window=2048)
    assert error.context_window == 2048
    assert error.min_required == 16384
    assert "2,048 tokens" in str(error)
    assert "16,384 tokens" in str(error)
    assert "ALLOW_SHORT_CONTEXT_WINDOWS" in str(error)
    assert "num_ctx" in str(error)


def test_llm_context_window_too_small_error_custom_min_required():
    """Test LLMContextWindowTooSmallError with custom min_required."""
    error = LLMContextWindowTooSmallError(context_window=4096, min_required=8192)
    assert error.context_window == 4096
    assert error.min_required == 8192
    assert "4,096 tokens" in str(error)
    assert "8,192 tokens" in str(error)


def test_llm_context_window_too_small_error_custom_message():
    """Test LLMContextWindowTooSmallError with custom message."""
    custom_message = "Custom error message"
    error = LLMContextWindowTooSmallError(
        context_window=2048,
        message=custom_message,
    )
    assert str(error) == custom_message
    assert error.context_window == 2048


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_raises_error_on_small_context_window(mock_get_model_info):
    """Test that LLM raises error when context window is too small."""
    mock_get_model_info.return_value = {"max_input_tokens": 2048}

    with pytest.raises(LLMContextWindowTooSmallError) as exc_info:
        LLM(
            model="ollama/test-model",
            api_key=SecretStr("test-key"),
            usage_id="test-llm",
        )

    assert exc_info.value.context_window == 2048
    assert exc_info.value.min_required == MIN_CONTEXT_WINDOW_TOKENS


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_allows_large_context_window(mock_get_model_info):
    """Test that LLM allows models with large enough context windows."""
    mock_get_model_info.return_value = {"max_input_tokens": 32768}

    # Should not raise
    llm = LLM(
        model="ollama/test-model",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )
    assert llm.max_input_tokens == 32768


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_allows_exact_minimum_context_window(mock_get_model_info):
    """Test that LLM allows models with exactly the minimum context window."""
    mock_get_model_info.return_value = {"max_input_tokens": MIN_CONTEXT_WINDOW_TOKENS}

    # Should not raise
    llm = LLM(
        model="ollama/test-model",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )
    assert llm.max_input_tokens == MIN_CONTEXT_WINDOW_TOKENS


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_skips_validation_when_context_window_unknown(mock_get_model_info):
    """Test that LLM skips validation when context window is unknown."""
    mock_get_model_info.return_value = None

    # Should not raise even though we don't know the context window
    llm = LLM(
        model="unknown/model",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )
    assert llm.max_input_tokens is None


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_respects_allow_short_context_windows_env_var(mock_get_model_info):
    """Test that ALLOW_SHORT_CONTEXT_WINDOWS env var bypasses validation."""
    mock_get_model_info.return_value = {"max_input_tokens": 2048}

    # Set the environment variable
    with patch.dict(os.environ, {ENV_ALLOW_SHORT_CONTEXT_WINDOWS: "true"}):
        # Should not raise
        llm = LLM(
            model="ollama/test-model",
            api_key=SecretStr("test-key"),
            usage_id="test-llm",
        )
        assert llm.max_input_tokens == 2048


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_respects_allow_short_context_windows_env_var_values(mock_get_model_info):
    """Test that various truthy values work for ALLOW_SHORT_CONTEXT_WINDOWS."""
    mock_get_model_info.return_value = {"max_input_tokens": 2048}

    for value in ["true", "TRUE", "True", "1", "yes", "YES"]:
        with patch.dict(os.environ, {ENV_ALLOW_SHORT_CONTEXT_WINDOWS: value}):
            # Should not raise
            llm = LLM(
                model="ollama/test-model",
                api_key=SecretStr("test-key"),
                usage_id="test-llm",
            )
            assert llm.max_input_tokens == 2048


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_rejects_non_truthy_env_var_values(mock_get_model_info):
    """Test that non-truthy values don't bypass validation."""
    mock_get_model_info.return_value = {"max_input_tokens": 2048}

    for value in ["false", "0", "no", ""]:
        with patch.dict(os.environ, {ENV_ALLOW_SHORT_CONTEXT_WINDOWS: value}):
            with pytest.raises(LLMContextWindowTooSmallError):
                LLM(
                    model="ollama/test-model",
                    api_key=SecretStr("test-key"),
                    usage_id="test-llm",
                )


@patch("openhands.sdk.llm.llm.get_litellm_model_info")
def test_llm_user_specified_max_input_tokens_bypasses_litellm(mock_get_model_info):
    """Test that user-specified max_input_tokens is used for validation."""
    # LiteLLM returns small context window
    mock_get_model_info.return_value = {"max_input_tokens": 2048}

    # But user specifies a larger one - should still use the smaller one
    # from LiteLLM since max_input_tokens is set during _init_model_info_and_caps
    # when it's None, but if user provides it, it's used first
    llm = LLM(
        model="ollama/test-model",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
        max_input_tokens=32768,  # User override
    )
    # User's value should be preserved
    assert llm.max_input_tokens == 32768


def test_min_context_window_constant():
    """Test that MIN_CONTEXT_WINDOW_TOKENS is set to expected value."""
    assert MIN_CONTEXT_WINDOW_TOKENS == 16384


def test_env_var_name_constant():
    """Test that ENV_ALLOW_SHORT_CONTEXT_WINDOWS is set correctly."""
    assert ENV_ALLOW_SHORT_CONTEXT_WINDOWS == "ALLOW_SHORT_CONTEXT_WINDOWS"
