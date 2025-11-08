"""Tests for SystemPromptEvent.visualize method."""

import copy

from litellm import ChatCompletionToolParam

from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.llm import TextContent


def test_visualize_no_data_mutation():
    """Test that visualize does not mutate the original event data."""
    # Create tool with long type field (edge case)
    original_tool = ChatCompletionToolParam(
        type="function_with_very_long_type_name_exceeding_thirty_characters",
        function={
            "name": "test_tool",
            "description": "Test description",
            "parameters": {"type": "object", "properties": {}},
        },
    )

    event = SystemPromptEvent(
        system_prompt=TextContent(text="Test system prompt"),
        tools=[original_tool],
    )

    # Store initial state
    initial_tool_state = copy.deepcopy(event.tools[0])

    # Call visualize multiple times
    for _ in range(3):
        _ = event.visualize

    # Verify no mutation occurred
    assert event.tools[0] == initial_tool_state
    assert len(event.tools[0]["type"]) == len(initial_tool_state["type"])


def test_visualize_parameter_truncation():
    """Test that long parameter JSON strings are truncated in display."""
    # Create tool with very long parameters
    long_params = {
        "type": "object",
        "properties": {
            f"param_{i}": {
                "type": "string",
                "description": f"Parameter {i} with very long description",
            }
            for i in range(50)
        },
        "required": [f"param_{i}" for i in range(25)],
    }

    tool = ChatCompletionToolParam(
        type="function",
        function={
            "name": "test_tool",
            "description": "Test tool",
            "parameters": long_params,
        },
    )

    event = SystemPromptEvent(
        system_prompt=TextContent(text="Test system prompt"),
        tools=[tool],
    )

    # Get visualization
    visualization = event.visualize
    visualization_text = visualization.plain

    # Find parameters line
    params_lines = [
        line for line in visualization_text.split("\n") if "Parameters:" in line
    ]
    assert len(params_lines) == 1

    params_text = params_lines[0].split("Parameters: ")[1]

    # Verify truncation
    assert len(params_text) <= 200
    assert params_text.endswith("...")


def test_visualize_string_truncation_logic():
    """Test the string truncation logic for tool fields."""
    # Create tool with long string fields that would be truncated
    tool = ChatCompletionToolParam(
        type="function_with_very_long_type_name_that_exceeds_thirty_characters",
        function={
            "name": "test_tool_with_very_long_name_exceeding_limit",
            "description": "This is a very long description that should be truncated",
            "parameters": {"type": "object", "properties": {}},
        },
    )

    event = SystemPromptEvent(
        system_prompt=TextContent(text="Test system prompt"),
        tools=[tool],
    )

    # Store original lengths
    original_type_len = len(tool["type"])
    original_name_len = len(tool["function"]["name"])  # type: ignore[index]
    original_desc_len = len(tool["function"]["description"])  # type: ignore[index]

    # Call visualize
    visualization = event.visualize
    visualization_text = visualization.plain

    # Verify original data unchanged
    assert len(event.tools[0]["type"]) == original_type_len
    assert len(event.tools[0]["function"]["name"]) == original_name_len  # type: ignore[index]
    assert len(event.tools[0]["function"]["description"]) == original_desc_len  # type: ignore[index]

    # Verify visualization contains truncated display
    assert "..." in visualization_text  # Some truncation occurred in display
