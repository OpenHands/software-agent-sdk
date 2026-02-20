"""Tests for LLM input validation and SDK corruption bugs.

Two types of tests:
1. XFAIL tests demonstrating SDK bugs (will pass when bugs are fixed)
2. Passing tests showing validation catches these issues before API calls

The XFAIL tests reproduce the actual conditions from production issues:
- Bug #1782: Session terminates, resumes, re-executes -> duplicate observation
- Bug #2127: Session terminates mid-tool-execution -> orphan tool_use
"""

import pytest

from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import LLMInputValidationError
from openhands.sdk.llm.validation import (
    AnthropicMessageValidator,
    OpenAIChatMessageValidator,
    OpenAIResponsesInputValidator,
    get_validator,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


# ============================================================================
# XFAIL tests - Reproduce actual production failure scenarios
# ============================================================================


class TestSessionTerminationBugs:
    """Tests reproducing bugs from session termination mid-execution.

    These simulate real production scenarios where:
    - A pod/session terminates unexpectedly during tool execution
    - On resume, get_unmatched_actions() returns actions that shouldn't be re-executed
    - Re-execution creates duplicate observations or corrupt message state
    """

    @pytest.mark.xfail(
        reason="Bug #2127: Session terminates mid-tool-call, orphan action sent to LLM",
        strict=True,
    )
    def test_session_crash_mid_execution_leaves_orphan_action(self):
        """Reproduce: Session crashes after action created but before observation.

        Real scenario:
        1. LLM returns tool_call, ActionEvent is created and persisted
        2. Tool starts executing
        3. Pod crashes/terminates BEFORE tool completes
        4. Observation is never created
        5. User resumes session and sends new message
        6. events_to_messages() includes the orphan action -> API error

        Expected: Orphan actions should be filtered from LLM messages
        """
        # Step 1-2: User message triggers action
        user_msg = MessageEvent(
            id="m1",
            llm_message=Message(role="user", content=[TextContent(text="run ls")]),
            source="user",
        )
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="Running command")],
            action=MCPToolAction(data={"command": "ls"}),
            tool_name="terminal",
            tool_call_id="call_crash",
            tool_call=MessageToolCall(
                id="call_crash",
                name="terminal",
                arguments='{"command":"ls"}',
                origin="completion",
            ),
            llm_response_id="r1",
            source="agent",
        )

        # Step 3-4: Pod crashes - NO observation created
        # Step 5: User resumes and sends new message
        user_msg2 = MessageEvent(
            id="m2",
            llm_message=Message(
                role="user", content=[TextContent(text="what happened?")]
            ),
            source="user",
        )

        # This is the event list after resume
        events = [user_msg, action, user_msg2]

        # Verify get_unmatched_actions correctly identifies the orphan
        unmatched = ConversationState.get_unmatched_actions(events)
        assert len(unmatched) == 1, "Should detect orphan action"
        assert unmatched[0].id == "a1"

        # Step 6: When preparing messages for LLM, orphan should be filtered
        messages = LLMConvertibleEvent.events_to_messages(events)

        # BUG: The orphan action is included, causing API error
        assistant_with_tools = [
            m for m in messages if m.role == "assistant" and m.tool_calls
        ]
        tool_results = [m for m in messages if m.role == "tool"]

        for msg in assistant_with_tools:
            for tc in msg.tool_calls or []:
                has_result = any(r.tool_call_id == tc.id for r in tool_results)
                assert has_result, (
                    f"Orphan tool_call {tc.id} sent to LLM without tool_result. "
                    "This causes API error: 'tool_use ids without tool_result blocks'"
                )

    @pytest.mark.xfail(
        reason="Bug #1782: Resume incorrectly re-executes action, creates duplicate",
        strict=True,
    )
    def test_resume_reexecutes_completed_action_creates_duplicate(self):
        """Reproduce: Resume re-executes action that already has observation.

        Real scenario:
        1. Action executes, observation is created
        2. Pod terminates before state is fully checkpointed
        3. On resume, get_unmatched_actions() incorrectly returns the action
           (because observation wasn't in the checkpoint)
        4. Action re-executes, creating a SECOND observation with same tool_call_id
        5. events_to_messages() produces duplicate tool_results -> API error

        Expected: Duplicate observations should be deduplicated
        """
        # Step 1: Normal execution - action with observation
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="Running command")],
            action=MCPToolAction(data={"command": "ls"}),
            tool_name="terminal",
            tool_call_id="call_dup",
            tool_call=MessageToolCall(
                id="call_dup",
                name="terminal",
                arguments='{"command":"ls"}',
                origin="completion",
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs1 = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("file1.txt", tool_name="terminal"),
            tool_name="terminal",
            tool_call_id="call_dup",
            action_id="a1",
            source="environment",
        )

        # Step 2-4: After resume, action re-executes creating duplicate
        obs2 = ObservationEvent(
            id="o2",
            observation=MCPToolObservation.from_text(
                "file1.txt (re-executed)", tool_name="terminal"
            ),
            tool_name="terminal",
            tool_call_id="call_dup",  # Same tool_call_id!
            action_id="a1",
            source="environment",
        )

        events = [action, obs1, obs2]

        # Step 5: Convert to messages - should deduplicate
        messages = LLMConvertibleEvent.events_to_messages(events)
        tool_results = [m for m in messages if m.role == "tool"]

        # BUG: Both observations become tool_results
        assert len(tool_results) == 1, (
            f"Expected 1 tool_result, got {len(tool_results)}. "
            "Duplicate observations should be deduplicated. "
            "This causes API error: 'unexpected tool_use_id in tool_result'"
        )


class TestValidatorFactory:
    """Test get_validator() returns correct validator by model and response_type."""

    def test_anthropic_completion(self):
        v = get_validator("claude-3-opus", response_type="completion")
        assert isinstance(v, AnthropicMessageValidator)

    def test_openai_completion(self):
        v = get_validator("gpt-4o", response_type="completion")
        assert isinstance(v, OpenAIChatMessageValidator)

    def test_responses_api(self):
        v = get_validator("gpt-4o", response_type="responses")
        assert isinstance(v, OpenAIResponsesInputValidator)


class TestAnthropicValidation:
    """Key Anthropic-specific validation rules."""

    def test_catches_duplicate_tool_result(self):
        """Issue #1782: Duplicate tool_result for same tool_use_id."""
        messages = [
            {"role": "user", "content": "test"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "a"},
                    {"type": "tool_result", "tool_use_id": "t1", "content": "b"},  # dup
                ],
            },
        ]
        errors = AnthropicMessageValidator().validate(messages, tools_defined=True)
        assert any("Duplicate" in e for e in errors)

    def test_catches_missing_tool_result(self):
        """Issue #2127: tool_use without tool_result."""
        messages = [
            {"role": "user", "content": "test"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
                ],
            },
            {"role": "user", "content": "continue"},  # no tool_result
        ]
        errors = AnthropicMessageValidator().validate(messages, tools_defined=True)
        assert any(
            "tool_result" in e.lower() or "unresolved" in e.lower() for e in errors
        )


class TestOpenAIChatValidation:
    """Key OpenAI Chat validation rules."""

    def test_catches_duplicate_tool_response(self):
        """Issue #1782: Duplicate tool response."""
        messages = [
            {"role": "user", "content": "test"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "a"},
            {"role": "tool", "tool_call_id": "c1", "content": "b"},  # dup
        ]
        errors = OpenAIChatMessageValidator().validate(messages, tools_defined=True)
        assert any("Duplicate" in e for e in errors)

    def test_catches_orphan_tool_call(self):
        """Issue #2127: tool_call without response."""
        messages = [
            {"role": "user", "content": "test"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    },
                ],
            },
            {"role": "user", "content": "continue"},  # no tool response
        ]
        errors = OpenAIChatMessageValidator().validate(messages, tools_defined=True)
        assert any("unresolved" in e.lower() for e in errors)


class TestResponsesValidation:
    """Key Responses API validation rules."""

    def test_catches_duplicate_function_output(self):
        """Duplicate function_call_output for same call_id."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "test"}],
            },
            {"type": "function_call", "call_id": "fc1", "name": "x", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "fc1", "output": "a"},
            {"type": "function_call_output", "call_id": "fc1", "output": "b"},  # dup
        ]
        errors = OpenAIResponsesInputValidator().validate(
            input_items, tools_defined=True
        )
        assert any("Duplicate" in e for e in errors)


class TestValidateOrRaise:
    """Test validate_or_raise raises LLMInputValidationError."""

    def test_raises_with_details(self):
        messages = [
            {"role": "tool", "tool_call_id": "orphan", "content": "x"},
        ]
        with pytest.raises(LLMInputValidationError) as exc:
            OpenAIChatMessageValidator().validate_or_raise(messages, tools_defined=True)
        assert exc.value.provider == "openai_chat"
        assert len(exc.value.errors) > 0
