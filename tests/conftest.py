"""Common test fixtures and utilities."""

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.tool import ToolExecutor


@pytest.fixture
def mock_llm():
    """Create a standard mock LLM instance for testing."""
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )


@pytest.fixture
def mock_tool():
    """Create a mock tool for testing."""

    class MockExecutor(ToolExecutor):
        def __call__(self, action, conversation=None):
            return MagicMock(output="mock output", metadata=MagicMock(exit_code=0))

    # Create a simple mock tool without complex dependencies
    mock_tool = MagicMock()
    mock_tool.name = "mock_tool"
    mock_tool.executor = MockExecutor()
    return mock_tool


def create_mock_litellm_response(
    content: str = "Test response",
    response_id: str = "test-id",
    model: str = "gpt-4o",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
):
    """Helper function to create properly structured LiteLLM mock responses.

    Args:
        content: Response content
        response_id: Unique response ID
        model: Model name
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        finish_reason: Reason for completion
    """
    from litellm.types.utils import (
        Choices,
        Message as LiteLLMMessage,
        ModelResponse,
        Usage,
    )

    # Create proper LiteLLM message
    message = LiteLLMMessage(content=content, role="assistant")

    # Create proper choice
    choice = Choices(finish_reason=finish_reason, index=0, message=message)

    # Create proper usage
    usage = Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )

    # Create proper ModelResponse
    response = ModelResponse(
        id=response_id,
        choices=[choice],
        created=1234567890,
        model=model,
        object="chat.completion",
        usage=usage,
    )

    return response


@pytest.fixture(autouse=True)
def suppress_logging(monkeypatch):
    """Suppress logging during tests to reduce noise."""
    mock_logger = MagicMock()
    monkeypatch.setattr("openhands.sdk.llm.llm.logger", mock_logger)
