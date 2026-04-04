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


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("think", {"thought": "analyzing the problem"}),
        ("file_editor", {"command": "view", "path": "/workspace/file.py"}),
    ],
)
def test_extract_summary_returns_empty_for_frontend_handled_tools(
    agent, tool_name, arguments
):
    """Test that tools with frontend translations return empty summary.

    Frontend displays translated titles like "Thinking" and "Reading <path>"
    for these tools, which is better than raw JSON.
    See: https://github.com/OpenHands/OpenHands/issues/13690
    """
    original_args = arguments.copy()

    result = agent._extract_summary(tool_name, arguments)

    assert result == ""
    assert "summary" not in arguments
    assert arguments == original_args


@pytest.mark.parametrize(
    "tool_name,arguments,summary",
    [
        ("think", {"thought": "analyzing the problem"}, "Planning approach"),
        (
            "file_editor",
            {"command": "view", "path": "/workspace/file.py"},
            "Read config",
        ),
    ],
)
def test_extract_summary_uses_llm_provided_for_frontend_handled_tools(
    agent, tool_name, arguments, summary
):
    """Test that LLM-provided summaries are still used for frontend-handled tools."""
    arguments["summary"] = summary

    result = agent._extract_summary(tool_name, arguments)

    assert result == summary
    assert "summary" not in arguments
