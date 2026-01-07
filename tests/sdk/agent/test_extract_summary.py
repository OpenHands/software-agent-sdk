"""Tests for Agent._extract_summary method."""

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.llm import LLM


@pytest.fixture
def agent():
    """Create a test agent."""
    return Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )


@pytest.mark.parametrize(
    "summary_value,expected_result",
    [
        # Valid summary provided - use it
        ("testing file system", "testing file system"),
        # No summary provided - generate default
        (None, 'test_tool: {"some_param": "value"}'),
        # Non-string summary - generate default
        (123, 'test_tool: {"some_param": "value"}'),
        # Empty or whitespace-only - generate default
        ("", 'test_tool: {"some_param": "value"}'),
        ("   ", 'test_tool: {"some_param": "value"}'),
    ],
)
def test_extract_summary(agent, summary_value, expected_result):
    """Test _extract_summary method with various scenarios."""
    arguments = {"some_param": "value"}
    if summary_value is not None:
        arguments["summary"] = summary_value

    result = agent._extract_summary("test_tool", arguments)
    assert result == expected_result
    assert "summary" not in arguments
