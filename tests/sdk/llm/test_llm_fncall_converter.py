"""Test for FunctionCallingConverter."""

import json
import textwrap
from typing import cast

import pytest
from litellm import ChatCompletionToolParam

from openhands.sdk.llm.exceptions import (
    FunctionCallConversionError,
    FunctionCallValidationError,
)
from openhands.sdk.llm.mixins.fn_call_converter import (
    STOP_WORDS,
    convert_fncall_messages_to_non_fncall_messages,
    convert_non_fncall_messages_to_fncall_messages,
    convert_tool_call_to_string,
    convert_tools_to_description,
)


FNCALL_TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Execute a bash command in the terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the interaction when the task is complete.",
        },
    },
]


def test_stop_words_defined():
    """Test that STOP_WORDS is properly defined."""
    assert isinstance(STOP_WORDS, list)
    assert len(STOP_WORDS) > 0
    assert all(isinstance(word, str) for word in STOP_WORDS)


def test_convert_fncall_to_non_fncall_basic():
    """Test basic conversion from function call messages to non-function call
    messages."""
    fncall_messages = [
        {"role": "user", "content": "Please run ls command"},
        {
            "role": "assistant",
            "content": "I'll run the ls command for you.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
        {"role": "tool", "content": "file1.txt\nfile2.txt", "tool_call_id": "call_123"},
    ]

    non_fncall_messages = convert_fncall_messages_to_non_fncall_messages(
        fncall_messages, FNCALL_TOOLS
    )

    assert isinstance(non_fncall_messages, list)
    assert len(non_fncall_messages) >= len(fncall_messages)

    # Check that tool calls are converted to text format
    assistant_msg = None
    for msg in non_fncall_messages:
        if msg.get("role") == "assistant" and "terminal" in str(msg.get("content", "")):
            assistant_msg = msg
            break

    assert assistant_msg is not None
    assert "terminal" in assistant_msg["content"]


def test_convert_non_fncall_to_fncall_basic():
    """Test basic conversion from non-function call messages to function call
    messages."""
    non_fncall_messages = [
        {"role": "user", "content": "Please run ls command"},
        {
            "role": "assistant",
            "content": (
                "I'll run the ls command for you.\n\n<function=terminal>\n"
                "<parameter=command>ls</parameter>\n</function>"
            ),
        },
    ]

    fncall_messages = convert_non_fncall_messages_to_fncall_messages(
        non_fncall_messages, FNCALL_TOOLS
    )

    assert isinstance(fncall_messages, list)
    assert len(fncall_messages) >= len(non_fncall_messages)

    # Check that function calls are properly converted
    assistant_msg = None
    for msg in fncall_messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            assistant_msg = msg
            break

    assert assistant_msg is not None
    assert "tool_calls" in assistant_msg
    assert len(assistant_msg["tool_calls"]) == 1
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "terminal"


def test_convert_fncall_to_non_fncall_with_in_context_learning():
    """Test conversion with in-context learning examples."""
    fncall_messages = [{"role": "user", "content": "Please run ls command"}]

    non_fncall_messages = convert_fncall_messages_to_non_fncall_messages(
        fncall_messages, FNCALL_TOOLS, add_in_context_learning_example=True
    )

    assert isinstance(non_fncall_messages, list)
    # Agent-sdk may combine examples into existing messages rather than creating
    # new ones
    assert len(non_fncall_messages) >= len(fncall_messages)

    # Check that examples are added to the content
    has_example = False
    for msg in non_fncall_messages:
        content = str(msg.get("content", "")).lower()
        if "example" in content or "start of example" in content:
            has_example = True
            break

    # Examples should be present when requested
    assert has_example, (
        "In-context learning examples should be added to message content"
    )


def test_convert_fncall_to_non_fncall_without_in_context_learning():
    """Test conversion without in-context learning examples."""
    fncall_messages = [{"role": "user", "content": "Please run ls command"}]

    non_fncall_messages = convert_fncall_messages_to_non_fncall_messages(
        fncall_messages, FNCALL_TOOLS, add_in_context_learning_example=False
    )

    assert isinstance(non_fncall_messages, list)
    # Without examples, should be same length or similar
    assert len(non_fncall_messages) >= len(fncall_messages)


def test_convert_with_multiple_tool_calls():
    """Test that multiple tool calls in one message raise an error."""
    fncall_messages = [
        {"role": "user", "content": "Please run ls and then pwd"},
        {
            "role": "assistant",
            "content": "I'll run both commands for you.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "ls"}',
                    },
                },
                {
                    "id": "call_456",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "pwd"}',
                    },
                },
            ],
        },
    ]

    # Agent-SDK doesn't support multiple tool calls per message
    with pytest.raises(
        FunctionCallConversionError, match="Expected exactly one tool call"
    ):
        convert_fncall_messages_to_non_fncall_messages(fncall_messages, FNCALL_TOOLS)


def test_convert_with_tool_response():
    """Test conversion including tool responses."""
    fncall_messages = [
        {"role": "user", "content": "Please run ls command"},
        {
            "role": "assistant",
            "content": "I'll run the ls command.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "file1.txt\nfile2.txt\nfolder1/",
            "tool_call_id": "call_123",
        },
        {
            "role": "assistant",
            "content": "The directory contains two files and one folder.",
        },
    ]

    non_fncall_messages = convert_fncall_messages_to_non_fncall_messages(
        fncall_messages, FNCALL_TOOLS
    )

    assert isinstance(non_fncall_messages, list)
    assert len(non_fncall_messages) >= 3  # At least user, assistant, final assistant

    # Check that tool response is incorporated
    has_tool_output = False
    for msg in non_fncall_messages:
        content = str(msg.get("content", ""))
        if "file1.txt" in content or "folder1" in content:
            has_tool_output = True
            break

    assert has_tool_output


def test_convert_roundtrip():
    """Test that conversion is somewhat reversible."""
    original_fncall = [
        {"role": "user", "content": "Please run ls command"},
        {
            "role": "assistant",
            "content": "I'll run the ls command.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
    ]

    # Convert to non-function call format
    non_fncall = convert_fncall_messages_to_non_fncall_messages(
        original_fncall, FNCALL_TOOLS
    )
    # Convert back to function call format
    back_to_fncall = convert_non_fncall_messages_to_fncall_messages(
        non_fncall, FNCALL_TOOLS
    )

    assert isinstance(back_to_fncall, list)

    # Check that we have tool calls in the result
    has_tool_calls = False
    for msg in back_to_fncall:
        if msg.get("tool_calls"):
            has_tool_calls = True
            break

    assert has_tool_calls


def test_convert_with_invalid_function_call():
    """Test handling of invalid function call format."""
    non_fncall_messages = [
        {"role": "user", "content": "Please run ls command"},
        {
            "role": "assistant",
            "content": (
                "I'll run the ls command.\n\n<function=invalid_function>\n"
                "<parameter=command>ls</parameter>\n</function>"
            ),
        },
    ]

    # This should handle invalid function calls gracefully
    try:
        fncall_messages = convert_non_fncall_messages_to_fncall_messages(
            non_fncall_messages, FNCALL_TOOLS
        )
        # If no exception, check that result is reasonable
        assert isinstance(fncall_messages, list)
    except (
        FunctionCallConversionError,
        FunctionCallValidationError,
        ValueError,
        KeyError,
    ):
        # These exceptions are acceptable for invalid function calls
        pass


def test_convert_with_malformed_parameters():
    """Test handling of malformed function parameters."""
    non_fncall_messages = [
        {"role": "user", "content": "Please run ls command"},
        {
            "role": "assistant",
            "content": (
                "I'll run the ls command.\n\n<function=terminal>\n"
                "<parameter=invalid_param>ls</parameter>\n</function>"
            ),
        },
    ]

    # This should handle malformed parameters gracefully
    try:
        fncall_messages = convert_non_fncall_messages_to_fncall_messages(
            non_fncall_messages, FNCALL_TOOLS
        )
        assert isinstance(fncall_messages, list)
    except (
        FunctionCallConversionError,
        FunctionCallValidationError,
        ValueError,
        KeyError,
    ):
        # These exceptions are acceptable for malformed parameters
        pass


def test_convert_empty_messages():
    """Test conversion with empty message list."""
    empty_messages = []
    non_fncall = convert_fncall_messages_to_non_fncall_messages(
        empty_messages, FNCALL_TOOLS
    )
    assert isinstance(non_fncall, list)
    fncall = convert_non_fncall_messages_to_fncall_messages(
        empty_messages, FNCALL_TOOLS
    )
    assert isinstance(fncall, list)


def test_convert_with_no_tools():
    """Test conversion with empty tools list."""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]

    non_fncall = convert_fncall_messages_to_non_fncall_messages(messages, [])
    assert isinstance(non_fncall, list)
    assert len(non_fncall) >= len(messages)

    fncall = convert_non_fncall_messages_to_fncall_messages(messages, [])
    assert isinstance(fncall, list)
    assert len(fncall) >= len(messages)


def test_convert_preserves_user_messages():
    """Test that user messages are preserved during conversion."""
    messages = [
        {"role": "user", "content": "Please help me with this task"},
        {"role": "assistant", "content": "I'll help you with that."},
    ]

    non_fncall = convert_fncall_messages_to_non_fncall_messages(messages, FNCALL_TOOLS)

    # Find user message in result
    user_msg = None
    for msg in non_fncall:
        if msg.get("role") == "user":
            user_msg = msg
            break

    assert user_msg is not None
    assert "Please help me with this task" in user_msg["content"]


def test_convert_with_system_message():
    """Test conversion with system messages."""
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Please run ls command"},
        {
            "role": "assistant",
            "content": "I'll run the ls command.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
    ]

    non_fncall = convert_fncall_messages_to_non_fncall_messages(messages, FNCALL_TOOLS)

    # System message should be preserved
    system_msg = None
    for msg in non_fncall:
        if msg.get("role") == "system":
            system_msg = msg
            break

    assert system_msg is not None
    assert "helpful assistant" in system_msg["content"]


def test_convert_with_finish_tool():
    """Test conversion with finish tool call."""
    fncall_messages = [
        {"role": "user", "content": "Please finish the task"},
        {
            "role": "assistant",
            "content": "Task completed.",
            "tool_calls": [
                {
                    "id": "call_finish",
                    "type": "function",
                    "function": {"name": "finish", "arguments": "{}"},
                }
            ],
        },
    ]

    non_fncall = convert_fncall_messages_to_non_fncall_messages(
        fncall_messages, FNCALL_TOOLS
    )

    assert isinstance(non_fncall, list)

    # Check that finish call is represented
    has_finish = False
    for msg in non_fncall:
        content = str(msg.get("content", ""))
        if "finish" in content.lower():
            has_finish = True
            break

    assert has_finish


def test_convert_tools_to_description_array_items():
    """Ensure array parameters with object items are formatted clearly."""
    tools = cast(
        list[ChatCompletionToolParam],
        [
            {
                "type": "function",
                "function": {
                    "name": "task_tracker",
                    "description": "Track task plans for execution.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The command to execute. `view` shows the current task list. `plan` creates or updates the task list based on provided requirements and progress. Always `view` the current list before making changes.",  # noqa: E501
                                "enum": ["view", "plan"],
                            },
                            "task_list": {
                                "type": "array",
                                "description": (
                                    "The full task list. Required parameter of `plan` command."  # noqa: E501
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {
                                            "type": "string",
                                            "description": "A brief title for the task.",  # noqa: E501
                                        },
                                        "notes": {
                                            "type": "string",
                                            "description": "Additional details or notes about the task.",  # noqa: E501
                                        },
                                        "status": {
                                            "type": "string",
                                            "description": (
                                                "The current status of the task. One of "  # noqa: E501
                                                "'todo', 'in_progress', or 'done'."
                                            ),
                                            "enum": ["todo", "in_progress", "done"],
                                        },
                                    },
                                    "required": ["title"],
                                },
                            },
                        },
                        "required": [],
                    },
                },
            }
        ],
    )

    description = convert_tools_to_description(tools)

    expected_command_line = (
        "  (1) command (string, optional): The command to execute. `view` shows the current task list. "  # noqa: E501
        "`plan` creates or updates the task list based on provided requirements and progress. "  # noqa: E501
        "Always `view` the current list before making changes.\n"
        "Allowed values: [`view`, `plan`]\n"
    )
    assert expected_command_line in description
    # Top-level parameter line should reflect the summarized array type
    assert (
        "  (2) task_list (array[object], optional): The full task list. Required parameter of `plan` command.\n"  # noqa: E501
        in description
    )
    # Nested structure should be shown via the generic recursive formatter
    assert "Object properties:" in description
    assert "- title (string, required): A brief title for the task." in description
    assert (
        "- notes (string, optional): Additional details or notes about the task."
        in description
    )
    assert (
        "- status (string, optional): The current status of the task. One of 'todo', 'in_progress', or 'done'."  # noqa: E501
        in description
    )
    # Nested enum values are described inline in the field description; no separate
    # "Allowed values" line is required.


@pytest.mark.parametrize(
    "tool_call, expected",
    [
        # Basic single parameter
        (
            {
                "id": "test_id",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": '{"command": "ls -la"}',
                },
            },
            ("<function=terminal>\n<parameter=command>ls -la</parameter>\n</function>"),
        ),
        # Multiple parameters with different types
        (
            {
                "id": "test_id",
                "type": "function",
                "function": {
                    "name": "file_editor",
                    "arguments": (
                        '{"command": "view", "path": "/test/file.py", '
                        '"view_range": [1, 10]}'
                    ),
                },
            },
            (
                "<function=file_editor>\n<parameter=command>view</parameter>\n"
                "<parameter=path>/test/file.py</parameter>\n"
                "<parameter=view_range>[1, 10]</parameter>\n</function>"
            ),
        ),
        # Indented code blocks (whitespace preservation)
        (
            {
                "id": "test_id",
                "type": "function",
                "function": {
                    "name": "file_editor",
                    "arguments": json.dumps(
                        {
                            "command": "str_replace",
                            "path": "/test/file.py",
                            "old_str": "def example():\n    pass",
                            "new_str": (
                                "def example():\n    # This is indented\n"
                                '    print("hello")\n    return True'
                            ),
                        }
                    ),
                },
            },
            (
                "<function=file_editor>\n<parameter=command>str_replace</parameter>\n"
                "<parameter=path>/test/file.py</parameter>\n<parameter=old_str>\n"
                "def example():\n    pass\n</parameter>\n<parameter=new_str>\n"
                'def example():\n    # This is indented\n    print("hello")\n'
                "    return True\n</parameter>\n</function>"
            ),
        ),
        # List parameter values
        (
            {
                "id": "test_id",
                "type": "function",
                "function": {
                    "name": "test_function",
                    "arguments": (
                        '{"command": "test", "path": "/test/file.py", '
                        '"tags": ["tag1", "tag2", "tag with spaces"]}'
                    ),
                },
            },
            (
                "<function=test_function>\n<parameter=command>test</parameter>\n"
                "<parameter=path>/test/file.py</parameter>\n"
                '<parameter=tags>["tag1", "tag2", "tag with spaces"]</parameter>\n'
                "</function>"
            ),
        ),
        # Dictionary parameter values
        (
            {
                "id": "test_id",
                "type": "function",
                "function": {
                    "name": "test_function",
                    "arguments": json.dumps(
                        {
                            "command": "test",
                            "path": "/test/file.py",
                            "metadata": {
                                "key1": "value1",
                                "key2": 42,
                                "nested": {"subkey": "subvalue"},
                            },
                        }
                    ),
                },
            },
            (
                "<function=test_function>\n<parameter=command>test</parameter>\n"
                "<parameter=path>/test/file.py</parameter>\n"
                '<parameter=metadata>{"key1": "value1", "key2": 42, '
                '"nested": {"subkey": "subvalue"}}</parameter>\n</function>'
            ),
        ),
    ],
)
def test_convert_tool_call_to_string_parameterized(tool_call, expected):
    """Test tool call to string conversion with various parameter types and formats."""
    converted = convert_tool_call_to_string(tool_call)
    assert converted == expected


def test_convert_fncall_messages_with_cache_control():
    """Test that cache_control is properly handled in tool messages."""
    messages = [
        {
            "role": "tool",
            "name": "test_tool",
            "content": [{"type": "text", "text": "test content"}],
            "cache_control": {"type": "ephemeral"},
            "tool_call_id": "call_123",
        }
    ]

    result = convert_fncall_messages_to_non_fncall_messages(messages, FNCALL_TOOLS)

    # Verify the result
    assert len(result) == 1
    assert result[0]["role"] == "user"

    # Check that cache_control is preserved in the converted message
    assert "cache_control" in result[0]["content"][-1]
    assert result[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    # Check that the tool result content is properly formatted
    assert (
        result[0]["content"][0]["text"]
        == "EXECUTION RESULT of [test_tool]:\ntest content"
    )


def test_convert_fncall_messages_without_cache_control():
    """Test that tool messages without cache_control work as expected."""
    messages = [
        {
            "role": "tool",
            "name": "test_tool",
            "content": [{"type": "text", "text": "test content"}],
            "tool_call_id": "call_123",
        }
    ]

    result = convert_fncall_messages_to_non_fncall_messages(messages, FNCALL_TOOLS)

    # Verify the result
    assert len(result) == 1
    assert result[0]["role"] == "user"

    # Check that no cache_control is added when not present
    assert "cache_control" not in result[0]["content"][-1]

    # Check that the tool result content is properly formatted
    assert (
        result[0]["content"][0]["text"]
        == "EXECUTION RESULT of [test_tool]:\ntest content"
    )


def test_convert_fncall_messages_with_image_url():
    """Test that convert_fncall_messages_to_non_fncall_messages handles image URLs
    correctly."""
    messages = [
        {
            "role": "tool",
            "name": "browser",
            "content": [
                {
                    "type": "text",
                    "text": "some browser tool results",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/gif;base64,R0lGODlhAQABAAAAACw="},
                },
            ],
            "tool_call_id": "call_123",
        }
    ]

    converted_messages = convert_fncall_messages_to_non_fncall_messages(
        messages, FNCALL_TOOLS
    )

    assert len(converted_messages) == 1
    assert converted_messages[0]["role"] == "user"
    assert len(converted_messages[0]["content"]) == len(messages[0]["content"])

    # Check that text content is properly formatted with tool execution result
    text_content = next(
        c for c in converted_messages[0]["content"] if c["type"] == "text"
    )
    assert text_content["text"] == (
        f"EXECUTION RESULT of [{messages[0]['name']}]:\n"
        f"{messages[0]['content'][0]['text']}"
    )

    # Check that image URL is preserved
    image_content = next(
        c for c in converted_messages[0]["content"] if c["type"] == "image_url"
    )
    assert (
        image_content["image_url"]["url"]
        == "data:image/gif;base64,R0lGODlhAQABAAAAACw="
    )


def test_convert_tools_to_description_nested_array():
    tools: list[ChatCompletionToolParam] = [
        {
            "type": "function",
            "function": {
                "name": "nested_array",
                "description": "Handle nested arrays",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "description": "List of entries",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "value": {
                                        "type": "integer",
                                        "description": "The numeric value",
                                    }
                                },
                                "required": ["value"],
                            },
                        }
                    },
                    "required": ["items"],
                },
            },
        }
    ]

    result = convert_tools_to_description(tools)

    expected = textwrap.dedent(
        """\
        ---- BEGIN FUNCTION #1: nested_array ----
        Description: Handle nested arrays
        Parameters:
          (1) items (array[object], required): List of entries
              Array items:
                Type: object
                  Object properties:
                    - value (integer, required): The numeric value
        ---- END FUNCTION #1 ----
        """
    )

    assert result.strip() == expected.strip()


def test_convert_tools_to_description_union_options():
    tools: list[ChatCompletionToolParam] = [
        {
            "type": "function",
            "function": {
                "name": "union_tool",
                "description": "Test union parameter",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filters": {
                            "description": "Supported filters",
                            "anyOf": [
                                {"type": "string", "description": "match by name"},
                                {"type": "integer", "description": "match by id"},
                            ],
                        }
                    },
                },
            },
        }
    ]

    result = convert_tools_to_description(tools)

    expected = textwrap.dedent(
        """\
        ---- BEGIN FUNCTION #1: union_tool ----
        Description: Test union parameter
        Parameters:
          (1) filters (string or integer, optional): Supported filters
              anyOf options:
                - string: match by name
                - integer: match by id
        ---- END FUNCTION #1 ----
        """
    )

    assert result.strip() == expected.strip()


def test_convert_tools_to_description_object_details():
    tools: list[ChatCompletionToolParam] = [
        {
            "type": "function",
            "function": {
                "name": "object_tool",
                "description": "Test object parameter",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "config": {
                            "type": "object",
                            "description": "Configuration payload",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Friendly name",
                                },
                                "thresholds": {
                                    "type": "array",
                                    "description": "Threshold list",
                                    "items": {"type": "number"},
                                },
                            },
                            "required": ["name"],
                            "additionalProperties": {
                                "type": "string",
                                "description": "Extra properties",
                            },
                        }
                    },
                    "required": ["config"],
                },
            },
        }
    ]

    result = convert_tools_to_description(tools)

    expected = textwrap.dedent(
        """\
        ---- BEGIN FUNCTION #1: object_tool ----
        Description: Test object parameter
        Parameters:
          (1) config (object, required): Configuration payload
              Object properties:
                - name (string, required): Friendly name
                - thresholds (array[number], optional): Threshold list
                  Array items:
                    Type: number
              Additional properties allowed: string
        ---- END FUNCTION #1 ----
        """
    )

    assert result.strip() == expected.strip()
