"""Tests that verify APIComplianceMonitor catches all 8 API compliance patterns.

These tests convert the Message-based patterns from the API compliance tests
into Event sequences and verify the monitor detects the expected violations.

This provides fast unit test coverage without requiring LLM API calls.
"""

import uuid

import pytest

from openhands.sdk.conversation.compliance import APIComplianceMonitor
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool import Observation
from tests.integration.api_compliance.patterns import (
    A01_UNMATCHED_TOOL_USE,
    A02_UNMATCHED_TOOL_RESULT,
    A03_INTERLEAVED_USER_MSG,
    A04_INTERLEAVED_ASST_MSG,
    A05_DUPLICATE_TOOL_CALL_ID,
    A06_WRONG_TOOL_CALL_ID,
    A07_PARALLEL_MISSING_RESULT,
    A08_PARALLEL_WRONG_ORDER,
    ALL_COMPLIANCE_PATTERNS,
    CompliancePattern,
)


class SimpleObservation(Observation):
    """Simple observation for testing."""

    result: str

    @property
    def to_llm_content(self) -> list[TextContent]:
        return [TextContent(text=self.result)]


def message_to_event(
    msg: Message,
    pending_tool_calls: dict[str, MessageToolCall],
) -> MessageEvent | ActionEvent | ObservationEvent | None:
    """Convert a Message to the appropriate Event type.

    Args:
        msg: The Message to convert.
        pending_tool_calls: Dict tracking tool_call_id -> MessageToolCall for
            pending actions. Updated in-place when we see tool_calls.

    Returns:
        The corresponding Event, or None for system messages.
    """
    if msg.role == "system":
        # Skip system messages - they don't become events
        return None

    if msg.role == "user":
        return MessageEvent(
            id=str(uuid.uuid4()),
            source="user",
            llm_message=msg,
        )

    if msg.role == "assistant":
        # Check if this assistant message has tool_calls
        if msg.tool_calls:
            # Convert to ActionEvent(s) - we'll just return the first one
            # and track all tool_calls
            for tc in msg.tool_calls:
                pending_tool_calls[tc.id] = tc

            # Return an ActionEvent for the first tool_call
            first_tc = msg.tool_calls[0]
            thought_text = ""
            if msg.content and isinstance(msg.content[0], TextContent):
                thought_text = msg.content[0].text
            return ActionEvent(
                id=str(uuid.uuid4()),
                source="agent",
                thought=[TextContent(text=thought_text)],
                tool_name=first_tc.name,
                tool_call_id=first_tc.id,
                tool_call=first_tc,
                llm_response_id=str(uuid.uuid4()),
            )
        else:
            # Regular assistant message
            return MessageEvent(
                id=str(uuid.uuid4()),
                source="agent",
                llm_message=msg,
            )

    if msg.role == "tool":
        # Tool result -> ObservationEvent
        tool_call_id = msg.tool_call_id
        assert tool_call_id is not None, "Tool message must have tool_call_id"

        # Get result text
        result_text = ""
        if msg.content:
            for content in msg.content:
                if isinstance(content, TextContent):
                    result_text = content.text
                    break

        return ObservationEvent(
            id=str(uuid.uuid4()),
            source="environment",
            tool_name=msg.name or "terminal",
            tool_call_id=tool_call_id,
            action_id=str(uuid.uuid4()),  # We don't track this precisely
            observation=SimpleObservation(result=result_text),
        )

    return None


def convert_pattern_to_events(
    pattern: CompliancePattern,
) -> list[MessageEvent | ActionEvent | ObservationEvent]:
    """Convert a compliance pattern's messages to events.

    For patterns with multiple parallel tool_calls, we generate one ActionEvent
    per tool_call (not just one per assistant message).

    Returns:
        List of events representing the pattern.
    """
    events: list[MessageEvent | ActionEvent | ObservationEvent] = []
    pending_tool_calls: dict[str, MessageToolCall] = {}

    for msg in pattern.messages:
        if msg.role == "assistant" and msg.tool_calls and len(msg.tool_calls) > 1:
            # Handle parallel tool calls - create ActionEvent for each
            thought_text = ""
            if msg.content and isinstance(msg.content[0], TextContent):
                thought_text = msg.content[0].text
            for tc in msg.tool_calls:
                pending_tool_calls[tc.id] = tc
                event = ActionEvent(
                    id=str(uuid.uuid4()),
                    source="agent",
                    thought=[TextContent(text=thought_text)],
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    tool_call=tc,
                    llm_response_id=str(uuid.uuid4()),
                )
                events.append(event)
        else:
            event = message_to_event(msg, pending_tool_calls)
            if event is not None:
                events.append(event)

    return events


def run_pattern_through_monitor(
    pattern: CompliancePattern,
) -> tuple[list[str], int]:
    """Run a pattern through the monitor and collect violations.

    Returns:
        Tuple of (list of violation property_names, index of first violation)
    """
    monitor = APIComplianceMonitor()
    events = convert_pattern_to_events(pattern)

    all_violations: list[str] = []
    first_violation_idx = -1

    for i, event in enumerate(events):
        violations = monitor.process_event(event)
        for v in violations:
            if first_violation_idx == -1:
                first_violation_idx = i
            all_violations.append(v.property_name)

    return all_violations, first_violation_idx


# =============================================================================
# Parametrized test for all patterns
# =============================================================================


@pytest.mark.parametrize(
    "pattern",
    ALL_COMPLIANCE_PATTERNS,
    ids=[p.name for p in ALL_COMPLIANCE_PATTERNS],
)
def test_monitor_detects_pattern(pattern: CompliancePattern):
    """Verify the monitor detects the expected violation for each pattern."""
    violations, _ = run_pattern_through_monitor(pattern)

    assert len(violations) > 0, f"Pattern '{pattern.name}' should trigger a violation"
    assert pattern.expected_violation in violations, (
        f"Pattern '{pattern.name}' should trigger '{pattern.expected_violation}', "
        f"but got: {violations}"
    )


# =============================================================================
# Individual pattern tests with detailed assertions
# =============================================================================


def test_a01_unmatched_tool_use():
    """Pattern a01: User message while tool call is pending."""
    violations, idx = run_pattern_through_monitor(A01_UNMATCHED_TOOL_USE)

    assert "interleaved_message" in violations
    # Violation should occur when user message arrives (4th event: after action)
    assert idx == 2  # 0=user, 1=action, 2=user (violation)


def test_a02_unmatched_tool_result():
    """Pattern a02: Tool result with unknown tool_call_id."""
    violations, idx = run_pattern_through_monitor(A02_UNMATCHED_TOOL_RESULT)

    assert "unmatched_tool_result" in violations
    # Violation should occur when orphan tool result arrives
    assert idx == 2  # 0=user, 1=assistant, 2=tool (violation)


def test_a03_interleaved_user_msg():
    """Pattern a03: User message between tool_use and tool_result."""
    violations, idx = run_pattern_through_monitor(A03_INTERLEAVED_USER_MSG)

    assert "interleaved_message" in violations
    # Violation on interleaved user message
    assert idx == 2  # 0=user, 1=action, 2=user (violation), 3=tool


def test_a04_interleaved_asst_msg():
    """Pattern a04: Assistant message between tool_use and tool_result."""
    violations, idx = run_pattern_through_monitor(A04_INTERLEAVED_ASST_MSG)

    assert "interleaved_message" in violations
    # Violation on interleaved assistant message
    assert idx == 2  # 0=user, 1=action, 2=assistant (violation), 3=tool


def test_a05_duplicate_tool_call_id():
    """Pattern a05: Second tool_result with same tool_call_id."""
    violations, idx = run_pattern_through_monitor(A05_DUPLICATE_TOOL_CALL_ID)

    assert "duplicate_tool_result" in violations


def test_a06_wrong_tool_call_id():
    """Pattern a06: Tool result references wrong (already completed) ID."""
    violations, idx = run_pattern_through_monitor(A06_WRONG_TOOL_CALL_ID)

    # This results in a duplicate because call_A_ls was already completed
    assert "duplicate_tool_result" in violations


def test_a07_parallel_missing_result():
    """Pattern a07: User message while parallel tool calls are pending."""
    violations, idx = run_pattern_through_monitor(A07_PARALLEL_MISSING_RESULT)

    assert "interleaved_message" in violations


def test_a08_parallel_wrong_order():
    """Pattern a08: Tool results arrive before tool_calls."""
    violations, idx = run_pattern_through_monitor(A08_PARALLEL_WRONG_ORDER)

    assert "unmatched_tool_result" in violations
    # First violation should be on first tool result (unknown ID at that point)
    assert idx == 1  # 0=user, 1=tool (violation)


# =============================================================================
# Sanity check: valid sequences should have no violations
# =============================================================================


def test_valid_sequence_no_violations():
    """A valid tool-call sequence should have no violations."""
    monitor = APIComplianceMonitor()

    # Create a valid sequence: user -> action -> observation -> user
    user1 = MessageEvent(
        id=str(uuid.uuid4()),
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="List files")]),
    )
    tool_call = MessageToolCall(
        id="call_valid",
        name="terminal",
        arguments='{"command": "ls"}',
        origin="completion",
    )
    action = ActionEvent(
        id=str(uuid.uuid4()),
        source="agent",
        thought=[TextContent(text="I'll list files")],
        tool_name="terminal",
        tool_call_id="call_valid",
        tool_call=tool_call,
        llm_response_id=str(uuid.uuid4()),
    )
    observation = ObservationEvent(
        id=str(uuid.uuid4()),
        source="environment",
        tool_name="terminal",
        tool_call_id="call_valid",
        action_id=action.id,
        observation=SimpleObservation(result="file1.txt\nfile2.txt"),
    )
    user2 = MessageEvent(
        id=str(uuid.uuid4()),
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="Thanks!")]),
    )

    all_violations = []
    for event in [user1, action, observation, user2]:
        all_violations.extend(monitor.process_event(event))

    assert len(all_violations) == 0, f"Valid sequence had violations: {all_violations}"
