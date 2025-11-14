"""Tests for FallbackRouter functionality."""

from unittest.mock import patch

import pytest
from litellm.exceptions import (
    APIConnectionError,
    RateLimitError,
    ServiceUnavailableError,
)
from litellm.types.utils import (
    Choices,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMServiceUnavailableError
from openhands.sdk.llm.router import FallbackRouter


def create_mock_response(content: str = "Test response", model: str = "test-model"):
    """Helper function to create properly structured mock responses."""
    return ModelResponse(
        id="test-id",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model=model,
        object="chat.completion",
        system_fingerprint="test",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.fixture
def primary_llm():
    """Create a primary LLM for testing."""
    return LLM(
        model="gpt-4",
        api_key=SecretStr("test-key"),
        usage_id="primary",
        num_retries=0,  # Disable retries for faster tests
    )


@pytest.fixture
def fallback_llm():
    """Create a fallback LLM for testing."""
    return LLM(
        model="gpt-3.5-turbo",
        api_key=SecretStr("test-key"),
        usage_id="fallback",
        num_retries=0,  # Disable retries for faster tests
    )


@pytest.fixture
def test_messages():
    """Create test messages."""
    return [Message(role="user", content=[TextContent(text="Hello")])]


def test_fallback_router_creation(primary_llm, fallback_llm):
    """Test that FallbackRouter can be created with a list of models."""
    router = FallbackRouter(
        usage_id="test-router",
        llms=[primary_llm, fallback_llm],
    )
    assert router.router_name == "fallback_router"
    assert len(router.llms) == 2
    assert router.llms[0] == primary_llm
    assert router.llms[1] == fallback_llm


def test_fallback_router_requires_at_least_one_llm():
    """Test that FallbackRouter requires at least one LLM."""
    with pytest.raises(ValueError, match="at least one LLM"):
        FallbackRouter(
            usage_id="test-router",
            llms=[],
        )


def test_fallback_router_success_with_primary(primary_llm, fallback_llm, test_messages):
    """Test that router uses primary model when it succeeds."""
    router = FallbackRouter(
        usage_id="test-router",
        llms=[primary_llm, fallback_llm],
    )

    mock_response = create_mock_response(content="Primary response", model="gpt-4")

    with patch.object(
        primary_llm, "_transport_call", return_value=mock_response
    ) as mock_primary:
        response = router.completion(messages=test_messages)

        # Verify primary was called
        mock_primary.assert_called_once()

        # Verify response is from primary
        assert isinstance(response.message.content[0], TextContent)
        assert response.message.content[0].text == "Primary response"
        assert router.active_llm == primary_llm


def test_fallback_router_falls_back_on_rate_limit(
    primary_llm, fallback_llm, test_messages
):
    """Test that router falls back to secondary model on rate limit error."""
    router = FallbackRouter(
        usage_id="test-router",
        llms=[primary_llm, fallback_llm],
    )

    mock_fallback_response = create_mock_response(
        content="Fallback response", model="gpt-3.5-turbo"
    )

    with (
        patch.object(
            primary_llm,
            "_transport_call",
            side_effect=RateLimitError(
                message="Rate limit exceeded",
                model="gpt-4",
                llm_provider="openai",
            ),
        ) as mock_primary,
        patch.object(
            fallback_llm, "_transport_call", return_value=mock_fallback_response
        ) as mock_fallback,
    ):
        response = router.completion(messages=test_messages)

        # Verify both were called
        mock_primary.assert_called_once()
        mock_fallback.assert_called_once()

        # Verify response is from fallback
        assert isinstance(response.message.content[0], TextContent)
        assert response.message.content[0].text == "Fallback response"
        assert router.active_llm == fallback_llm


def test_fallback_router_falls_back_on_connection_error(
    primary_llm, fallback_llm, test_messages
):
    """Test that router falls back on API connection error."""
    router = FallbackRouter(
        usage_id="test-router",
        llms=[primary_llm, fallback_llm],
    )

    mock_fallback_response = create_mock_response(
        content="Fallback response", model="gpt-3.5-turbo"
    )

    with (
        patch.object(
            primary_llm,
            "_transport_call",
            side_effect=APIConnectionError(
                message="Connection failed",
                model="gpt-4",
                llm_provider="openai",
            ),
        ),
        patch.object(
            fallback_llm, "_transport_call", return_value=mock_fallback_response
        ),
    ):
        response = router.completion(messages=test_messages)
        assert isinstance(response.message.content[0], TextContent)
        assert response.message.content[0].text == "Fallback response"
        assert router.active_llm == fallback_llm


def test_fallback_router_raises_when_all_fail(primary_llm, fallback_llm, test_messages):
    """Test that router raises exception when all models fail."""
    router = FallbackRouter(
        usage_id="test-router",
        llms=[primary_llm, fallback_llm],
    )

    with (
        patch.object(
            primary_llm,
            "_transport_call",
            side_effect=ServiceUnavailableError(
                message="Service unavailable",
                model="gpt-4",
                llm_provider="openai",
            ),
        ),
        patch.object(
            fallback_llm,
            "_transport_call",
            side_effect=ServiceUnavailableError(
                message="Service unavailable",
                model="gpt-3.5-turbo",
                llm_provider="openai",
            ),
        ),
    ):
        with pytest.raises(LLMServiceUnavailableError):
            router.completion(messages=test_messages)


def test_fallback_router_with_multiple_fallbacks(test_messages):
    """Test router with multiple fallback models."""
    primary = LLM(
        model="gpt-4",
        api_key=SecretStr("test-key"),
        usage_id="primary",
        num_retries=0,
    )
    fallback1 = LLM(
        model="gpt-3.5-turbo",
        api_key=SecretStr("test-key"),
        usage_id="fallback1",
        num_retries=0,
    )
    fallback2 = LLM(
        model="gpt-3.5-turbo-16k",
        api_key=SecretStr("test-key"),
        usage_id="fallback2",
        num_retries=0,
    )

    router = FallbackRouter(
        usage_id="test-router",
        llms=[primary, fallback1, fallback2],
    )

    mock_response = create_mock_response(
        content="Fallback2 response", model="gpt-3.5-turbo-16k"
    )

    with (
        patch.object(
            primary,
            "_transport_call",
            side_effect=RateLimitError(
                message="Rate limit", model="gpt-4", llm_provider="openai"
            ),
        ) as mock_primary,
        patch.object(
            fallback1,
            "_transport_call",
            side_effect=RateLimitError(
                message="Rate limit", model="gpt-3.5-turbo", llm_provider="openai"
            ),
        ) as mock_fallback1,
        patch.object(
            fallback2, "_transport_call", return_value=mock_response
        ) as mock_fallback2,
    ):
        response = router.completion(messages=test_messages)

        # Verify all three were tried
        mock_primary.assert_called_once()
        mock_fallback1.assert_called_once()
        mock_fallback2.assert_called_once()

        # Verify response is from fallback2
        assert isinstance(response.message.content[0], TextContent)
        assert response.message.content[0].text == "Fallback2 response"
        assert router.active_llm == fallback2


def test_fallback_router_select_llm_returns_first(primary_llm, fallback_llm):
    """Test that select_llm always returns the first LLM's key."""
    router = FallbackRouter(
        usage_id="test-router",
        llms=[primary_llm, fallback_llm],
    )

    messages = [Message(role="user", content=[TextContent(text="Test")])]
    selected = router.select_llm(messages)
    assert selected == "llm_0"
