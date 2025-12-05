"""Tests for Agent._extract_summary method.

This module tests the _extract_summary method which handles extraction
and validation of summary parameters from tool arguments.

Summary field is always requested but optional - if invalid, returns None.
"""

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
        # Valid summary provided
        ("testing file system", "testing file system"),
        ("checking logs for errors", "checking logs for errors"),
        # No summary provided - returns None
        (None, None),
        # Non-string summary - returns None
        (123, None),
        (["list", "of", "words"], None),
        ({"type": "dict"}, None),
        # Empty or whitespace-only - returns None
        ("", None),
        ("   ", None),
    ],
)
def test_extract_summary(agent, summary_value, expected_result):
    """Test _extract_summary method with various scenarios."""
    # Prepare arguments
    arguments = {"some_param": "value"}
    if summary_value is not None:
        arguments["summary"] = summary_value

    result = agent._extract_summary(arguments)
    assert result == expected_result

    # Verify that summary was popped from arguments
    assert "summary" not in arguments
    # Verify other arguments remain
    assert arguments["some_param"] == "value"


def test_extract_summary_arguments_mutation(agent):
    """Test that arguments dict is properly mutated (summary is popped)."""
    # Test with summary present
    arguments = {"param1": "value1", "summary": "reading file", "param2": "value2"}
    original_args = arguments.copy()

    result = agent._extract_summary(arguments)

    # Verify result
    assert result == "reading file"

    # Verify summary was popped
    assert "summary" not in arguments

    # Verify other parameters remain
    assert arguments["param1"] == original_args["param1"]
    assert arguments["param2"] == original_args["param2"]
    assert len(arguments) == 2  # Only 2 params should remain
