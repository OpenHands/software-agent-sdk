"""Tests for Agent._extract_summary method.

This module tests the _extract_summary method which handles extraction
and validation of summary parameters from tool arguments.
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
    "enable_summaries,summary_value,expected_result,should_raise",
    [
        # Case 1: summaries enabled, valid summary provided
        (True, "testing file system", "testing file system", False),
        (True, "checking logs for errors", "checking logs for errors", False),
        # Case 2: summaries enabled, no summary provided - should raise
        (True, None, None, True),
        # Case 3: summaries disabled, summary provided - still extract it
        (False, "should be ignored", "should be ignored", False),
        # Case 4: summaries disabled, no summary - return None
        (False, None, None, False),
        # Case 5: summaries enabled, non-string summary - should raise
        (True, 123, None, True),
        (True, ["list", "of", "words"], None, True),
        (True, {"type": "dict"}, None, True),
    ],
)
def test_extract_summary(
    agent, enable_summaries, summary_value, expected_result, should_raise
):
    """Test _extract_summary method with various scenarios."""
    # Prepare arguments
    arguments = {"some_param": "value"}
    if summary_value is not None:
        arguments["summary"] = summary_value

    if should_raise:
        with pytest.raises(ValueError):
            agent._extract_summary(arguments, enable_summaries)
    else:
        result = agent._extract_summary(arguments, enable_summaries)
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

    result = agent._extract_summary(arguments, True)

    # Verify result
    assert result == "reading file"

    # Verify summary was popped
    assert "summary" not in arguments

    # Verify other parameters remain
    assert arguments["param1"] == original_args["param1"]
    assert arguments["param2"] == original_args["param2"]
    assert len(arguments) == 2  # Only 2 params should remain


def test_extract_summary_with_empty_arguments(agent):
    """Test _extract_summary with empty arguments dict."""
    # Summaries disabled, empty arguments
    arguments = {}
    result = agent._extract_summary(arguments, False)

    # Should return None when summaries disabled and no summary
    assert result is None
    assert arguments == {}  # Should remain empty


def test_extract_summary_enabled_with_empty_arguments(agent):
    """Test _extract_summary with empty arguments when summaries enabled."""
    # Summaries enabled, but no summary provided - should raise
    arguments = {}

    with pytest.raises(ValueError, match="Summary field is required"):
        agent._extract_summary(arguments, True)


def test_extract_summary_empty_string(agent):
    """Test _extract_summary with empty string."""
    # Empty string should raise ValueError when summaries enabled
    arguments = {"summary": ""}

    with pytest.raises(ValueError, match="Summary cannot be empty"):
        agent._extract_summary(arguments, True)


def test_extract_summary_whitespace_only(agent):
    """Test _extract_summary with whitespace-only string."""
    # Whitespace-only string should raise ValueError when summaries enabled
    arguments = {"summary": "   "}

    with pytest.raises(ValueError, match="Summary cannot be empty"):
        agent._extract_summary(arguments, True)
