"""API Compliance Pattern Definitions.

This module defines the 8 malformed message patterns (a01-a08) that test
API compliance. Each pattern is a list of Message objects representing
a malformed conversation sequence.

These patterns are used by:
1. Integration tests that verify how LLM APIs respond to malformed input
2. Unit tests that verify the APIComplianceMonitor catches these violations
"""

from dataclasses import dataclass

from openhands.sdk.llm import Message, MessageToolCall, TextContent


@dataclass
class CompliancePattern:
    """A compliance test pattern with metadata."""

    name: str
    description: str
    messages: list[Message]
    expected_violation: str  # The violation property_name we expect


# =============================================================================
# Pattern a01: Unmatched tool_use
# =============================================================================
A01_UNMATCHED_TOOL_USE = CompliancePattern(
    name="unmatched_tool_use",
    description=(
        "Conversation where an assistant message contains a tool_use "
        "(tool_calls), but no tool_result follows before the next user message."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[TextContent(text="List the files in the current directory.")],
        ),
        # Assistant message with tool_use
        Message(
            role="assistant",
            content=[TextContent(text="I'll list the files for you.")],
            tool_calls=[
                MessageToolCall(
                    id="call_abc123",
                    name="terminal",
                    arguments='{"command": "ls -la"}',
                    origin="completion",
                )
            ],
        ),
        # NOTE: No tool_result follows! Directly another user message.
        Message(
            role="user",
            content=[TextContent(text="What was the result?")],
        ),
    ],
    expected_violation="interleaved_message",
)


# =============================================================================
# Pattern a02: Unmatched tool_result
# =============================================================================
A02_UNMATCHED_TOOL_RESULT = CompliancePattern(
    name="unmatched_tool_result",
    description=(
        "Conversation where a tool_result message references a tool_call_id "
        "that doesn't exist in any prior assistant message's tool_calls."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[TextContent(text="List the files in the current directory.")],
        ),
        # Assistant message WITHOUT tool_use
        Message(
            role="assistant",
            content=[TextContent(text="I can help you list files. What directory?")],
        ),
        # Tool result that references a non-existent tool_call_id
        Message(
            role="tool",
            content=[TextContent(text="file1.txt\nfile2.txt\nfile3.txt")],
            tool_call_id="call_nonexistent_xyz",
            name="terminal",
        ),
    ],
    expected_violation="unmatched_tool_result",
)


# =============================================================================
# Pattern a03: Interleaved user message
# =============================================================================
A03_INTERLEAVED_USER_MSG = CompliancePattern(
    name="interleaved_user_message",
    description=(
        "Conversation where a user message appears between a tool_use "
        "(in assistant message) and its corresponding tool_result."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[TextContent(text="List the files in the current directory.")],
        ),
        # Assistant message with tool_use
        Message(
            role="assistant",
            content=[TextContent(text="I'll list the files for you.")],
            tool_calls=[
                MessageToolCall(
                    id="call_abc123",
                    name="terminal",
                    arguments='{"command": "ls -la"}',
                    origin="completion",
                )
            ],
        ),
        # INTERLEAVED: User message before tool_result
        Message(
            role="user",
            content=[TextContent(text="Actually, can you also show hidden files?")],
        ),
        # Tool result comes AFTER the interleaved user message
        Message(
            role="tool",
            content=[TextContent(text="file1.txt\nfile2.txt")],
            tool_call_id="call_abc123",
            name="terminal",
        ),
    ],
    expected_violation="interleaved_message",
)


# =============================================================================
# Pattern a04: Interleaved assistant message
# =============================================================================
A04_INTERLEAVED_ASST_MSG = CompliancePattern(
    name="interleaved_assistant_message",
    description=(
        "Conversation where an assistant message (without tool_calls) appears "
        "between a tool_use and its corresponding tool_result."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[TextContent(text="List the files in the current directory.")],
        ),
        # First assistant message with tool_use
        Message(
            role="assistant",
            content=[TextContent(text="I'll list the files for you.")],
            tool_calls=[
                MessageToolCall(
                    id="call_abc123",
                    name="terminal",
                    arguments='{"command": "ls -la"}',
                    origin="completion",
                )
            ],
        ),
        # INTERLEAVED: Another assistant message without tool_calls
        Message(
            role="assistant",
            content=[TextContent(text="The command is running...")],
        ),
        # Tool result comes AFTER the interleaved assistant message
        Message(
            role="tool",
            content=[TextContent(text="file1.txt\nfile2.txt")],
            tool_call_id="call_abc123",
            name="terminal",
        ),
    ],
    expected_violation="interleaved_message",
)


# =============================================================================
# Pattern a05: Duplicate tool_call_id
# =============================================================================
A05_DUPLICATE_TOOL_CALL_ID = CompliancePattern(
    name="duplicate_tool_call_id",
    description=(
        "Conversation where two tool_result messages have the same tool_call_id, "
        "meaning multiple results are provided for a single tool_use."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[TextContent(text="List the files in the current directory.")],
        ),
        # Assistant message with tool_use
        Message(
            role="assistant",
            content=[TextContent(text="I'll list the files for you.")],
            tool_calls=[
                MessageToolCall(
                    id="call_abc123",
                    name="terminal",
                    arguments='{"command": "ls -la"}',
                    origin="completion",
                )
            ],
        ),
        # First tool result (correct)
        Message(
            role="tool",
            content=[TextContent(text="file1.txt\nfile2.txt")],
            tool_call_id="call_abc123",
            name="terminal",
        ),
        # Some intervening messages
        Message(
            role="user",
            content=[TextContent(text="Thanks! Now what?")],
        ),
        Message(
            role="assistant",
            content=[
                TextContent(text="You're welcome! Let me know if you need anything.")
            ],
        ),
        Message(
            role="user",
            content=[TextContent(text="Actually, show me the files again.")],
        ),
        # DUPLICATE: Second tool result with SAME tool_call_id
        Message(
            role="tool",
            content=[TextContent(text="file1.txt\nfile2.txt\nfile3.txt")],
            tool_call_id="call_abc123",  # Same ID as before!
            name="terminal",
        ),
    ],
    expected_violation="duplicate_tool_result",
)


# =============================================================================
# Pattern a06: Wrong tool_call_id
# =============================================================================
A06_WRONG_TOOL_CALL_ID = CompliancePattern(
    name="wrong_tool_call_id",
    description=(
        "Conversation where a tool_result references the wrong tool_call_id "
        "(one that has already been completed)."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[TextContent(text="Run two commands: ls and pwd")],
        ),
        # First assistant message with tool_use (id=A)
        Message(
            role="assistant",
            content=[TextContent(text="I'll run ls first.")],
            tool_calls=[
                MessageToolCall(
                    id="call_A_ls",
                    name="terminal",
                    arguments='{"command": "ls"}',
                    origin="completion",
                )
            ],
        ),
        # First tool result - CORRECT
        Message(
            role="tool",
            content=[TextContent(text="file1.txt\nfile2.txt")],
            tool_call_id="call_A_ls",
            name="terminal",
        ),
        # Second assistant message with tool_use (id=B)
        Message(
            role="assistant",
            content=[TextContent(text="Now I'll run pwd.")],
            tool_calls=[
                MessageToolCall(
                    id="call_B_pwd",
                    name="terminal",
                    arguments='{"command": "pwd"}',
                    origin="completion",
                )
            ],
        ),
        # Second tool result - WRONG ID (references first tool_use which is done)
        Message(
            role="tool",
            content=[TextContent(text="/home/user/project")],
            tool_call_id="call_A_ls",  # Wrong! Should be call_B_pwd
            name="terminal",
        ),
    ],
    expected_violation="duplicate_tool_result",  # It's a duplicate of completed ID
)


# =============================================================================
# Pattern a07: Parallel missing result
# =============================================================================
A07_PARALLEL_MISSING_RESULT = CompliancePattern(
    name="parallel_missing_result",
    description=(
        "Conversation where an assistant message contains multiple parallel "
        "tool_calls, but only some of them have corresponding tool_results."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[
                TextContent(text="Get the weather in San Francisco, Tokyo, and Paris.")
            ],
        ),
        # Assistant message with THREE parallel tool_calls
        Message(
            role="assistant",
            content=[TextContent(text="I'll check the weather in all three cities.")],
            tool_calls=[
                MessageToolCall(
                    id="call_sf",
                    name="terminal",
                    arguments='{"command": "weather sf"}',
                    origin="completion",
                ),
                MessageToolCall(
                    id="call_tokyo",
                    name="terminal",
                    arguments='{"command": "weather tokyo"}',
                    origin="completion",
                ),
                MessageToolCall(
                    id="call_paris",
                    name="terminal",
                    arguments='{"command": "weather paris"}',
                    origin="completion",
                ),
            ],
        ),
        # Tool result for SF - provided
        Message(
            role="tool",
            content=[TextContent(text="San Francisco: 65°F, Sunny")],
            tool_call_id="call_sf",
            name="terminal",
        ),
        # Tool result for Tokyo - provided
        Message(
            role="tool",
            content=[TextContent(text="Tokyo: 72°F, Cloudy")],
            tool_call_id="call_tokyo",
            name="terminal",
        ),
        # NOTE: Tool result for Paris is MISSING!
        # Next user message arrives before Paris result
        Message(
            role="user",
            content=[TextContent(text="What about Paris?")],
        ),
    ],
    expected_violation="interleaved_message",
)


# =============================================================================
# Pattern a08: Parallel wrong order
# =============================================================================
A08_PARALLEL_WRONG_ORDER = CompliancePattern(
    name="parallel_wrong_order",
    description=(
        "Conversation where tool_results appear before the assistant message "
        "that contains the corresponding tool_calls."
    ),
    messages=[
        Message(
            role="system",
            content=[TextContent(text="You are a helpful assistant.")],
        ),
        Message(
            role="user",
            content=[TextContent(text="Check the weather in SF and Tokyo.")],
        ),
        # Tool results appear FIRST (wrong!)
        Message(
            role="tool",
            content=[TextContent(text="San Francisco: 65°F, Sunny")],
            tool_call_id="call_sf",
            name="terminal",
        ),
        Message(
            role="tool",
            content=[TextContent(text="Tokyo: 72°F, Cloudy")],
            tool_call_id="call_tokyo",
            name="terminal",
        ),
        # Assistant message with tool_calls comes AFTER tool_results
        Message(
            role="assistant",
            content=[TextContent(text="I'll check both cities.")],
            tool_calls=[
                MessageToolCall(
                    id="call_sf",
                    name="terminal",
                    arguments='{"command": "weather sf"}',
                    origin="completion",
                ),
                MessageToolCall(
                    id="call_tokyo",
                    name="terminal",
                    arguments='{"command": "weather tokyo"}',
                    origin="completion",
                ),
            ],
        ),
    ],
    expected_violation="unmatched_tool_result",
)


# All patterns for iteration
ALL_COMPLIANCE_PATTERNS = [
    A01_UNMATCHED_TOOL_USE,
    A02_UNMATCHED_TOOL_RESULT,
    A03_INTERLEAVED_USER_MSG,
    A04_INTERLEAVED_ASST_MSG,
    A05_DUPLICATE_TOOL_CALL_ID,
    A06_WRONG_TOOL_CALL_ID,
    A07_PARALLEL_MISSING_RESULT,
    A08_PARALLEL_WRONG_ORDER,
]
