"""Tests for events_to_messages conversion in openhands-sdk/event/base.py."""  # type: ignore

import json
from collections.abc import Sequence
from typing import cast

import pytest

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.condenser import CondensationSummaryEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import (
    ImageContent,
    Message,
    MessageToolCall,
    ReasoningItemModel,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.tool import Action, Observation


class EventsToMessagesMockAction(Action):
    """Mock action for testing."""

    command: str


class EventsToMessagesMockObservation(Observation):
    """Mock observation for testing."""

    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


def create_tool_call(
    call_id: str, function_name: str, arguments: dict
) -> MessageToolCall:
    """Helper to create a MessageToolCall."""
    return MessageToolCall(
        id=call_id,
        name=function_name,
        arguments=json.dumps(arguments),
        origin="completion",
    )


def create_action_event(
    thought_text: str,
    tool_name: str,
    tool_call_id: str,
    llm_response_id: str,
    action_args: dict,
) -> ActionEvent:
    """Helper to create an ActionEvent."""
    action = EventsToMessagesMockAction(command=action_args.get("command", "test"))
    tool_call = create_tool_call(tool_call_id, tool_name, action_args)

    return ActionEvent(
        source="agent",
        thought=[TextContent(text=thought_text)],
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id=llm_response_id,
    )


class TestEventsToMessages:
    """Test cases for events_to_messages function."""

    def test_empty_events_list(self):
        """Test conversion of empty events list."""
        events = []
        messages = LLMConvertibleEvent.events_to_messages(events)
        assert messages == []

    def test_single_message_event(self):
        """Test conversion of single MessageEvent."""
        message_event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="Hello, how are you?")]
            ),
        )

        events = cast(list[LLMConvertibleEvent], [message_event])
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert len(messages) == 1
        assert messages[0].role == "user"
        assert len(messages[0].content) == 1
        assert isinstance(messages[0].content[0], TextContent)
        assert messages[0].content[0].text == "Hello, how are you?"

    def test_consecutive_user_message_events_are_merged(self):
        """Synthetic user context should not create adjacent user turns."""
        user_message = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="Implement the feature")]
            ),
        )
        user_context = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Relevant project context")],
            ),
        )
        assistant_message = MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant", content=[TextContent(text="I'll take a look.")]
            ),
        )

        events = cast(
            list[LLMConvertibleEvent], [user_message, user_context, assistant_message]
        )
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert [message.role for message in messages] == ["user", "assistant"]
        assert [
            content.text
            for content in messages[0].content
            if isinstance(content, TextContent)
        ] == [
            "Implement the feature",
            "Relevant project context",
        ]

    def test_user_message_merge_cascades_across_three_events(self):
        """A merged message stays a plain user turn, so the merge cascades."""
        first = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="one")]),
        )
        second = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="two")]),
        )
        third = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="three")]),
        )

        events = cast(list[LLMConvertibleEvent], [first, second, third])
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert [message.role for message in messages] == ["user"]
        assert [content.text for content in messages[0].content] == [  # type: ignore
            "one",
            "two",
            "three",
        ]

    def test_user_message_merge_preserves_mixed_content_order(self):
        """Merged content concatenates in log order, text and images alike."""
        text_first = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="what is in this image?")]
            ),
        )
        image_second = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[ImageContent(image_urls=["http://example.com/a.png"])],
            ),
        )

        events = cast(list[LLMConvertibleEvent], [text_first, image_second])
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert len(messages) == 1
        assert [type(content) for content in messages[0].content] == [
            TextContent,
            ImageContent,
        ]

    def test_events_to_messages_does_not_mutate_input_events(self):
        """Merging must not leak into the source events' stored messages."""
        first = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="one")]),
        )
        second = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="two")]),
        )
        events = cast(list[LLMConvertibleEvent], [first, second])

        first_pass = LLMConvertibleEvent.events_to_messages(events)
        assert len(first_pass[0].content) == 2

        # The events' own messages are untouched, so a second projection over
        # the same log produces the same output instead of merging twice.
        assert len(first.llm_message.content) == 1
        assert len(second.llm_message.content) == 1
        second_pass = LLMConvertibleEvent.events_to_messages(events)
        assert len(second_pass[0].content) == 2

    def test_condensation_summary_merges_with_adjacent_user_message(self):
        """A summary event converts to a plain user message, so it coalesces.

        The summarizing condenser inserts the summary where the forgotten
        block began (summary_offset == forgetting_start), and the kept suffix
        can legally begin with a user message — so this adjacency is
        reachable, and the fusion erases the summary's structural boundary.
        """
        summary_event = CondensationSummaryEvent(
            summary="Earlier we refactored the module."
        )
        user_after = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="Now continue.")]
            ),
        )

        events = cast(list[LLMConvertibleEvent], [summary_event, user_after])
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert len(messages) == 1
        assert messages[0].role == "user"
        assert [
            content.text
            for content in messages[0].content
            if isinstance(content, TextContent)
        ] == ["Earlier we refactored the module.", "Now continue."]

    def test_user_messages_with_tool_call_id_not_merged(self):
        """Tool-result user messages must not be coalesced."""
        result = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Tool output")],
                tool_call_id="call_abc",
            ),
        )
        context = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Additional context")],
            ),
        )

        events = cast(list[LLMConvertibleEvent], [result, context])
        messages = LLMConvertibleEvent.events_to_messages(events)
        assert len(messages) == 2

    def test_user_messages_with_tool_calls_not_merged(self):
        """User messages containing tool calls must not be coalesced."""
        tool_msg = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Call this tool")],
                tool_calls=[
                    MessageToolCall(
                        id="call_1",
                        name="search",
                        arguments="{}",
                        origin="completion",
                    )
                ],
            ),
        )
        plain = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Plain message")],
            ),
        )

        events = cast(list[LLMConvertibleEvent], [tool_msg, plain])
        messages = LLMConvertibleEvent.events_to_messages(events)
        assert len(messages) == 2

    def test_user_messages_with_name_not_merged(self):
        """Named user messages must not be coalesced."""
        named = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Named output")],
                name="search_results",
            ),
        )
        plain = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Plain message")],
            ),
        )

        events = cast(list[LLMConvertibleEvent], [named, plain])
        messages = LLMConvertibleEvent.events_to_messages(events)
        assert len(messages) == 2

    def test_mixed_role_messages_not_merged(self):
        """Messages with different roles must not be coalesced."""
        assistant_msg = MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text="Assistant reply")],
            ),
        )
        user_msg = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="User message")],
            ),
        )

        events = cast(list[LLMConvertibleEvent], [assistant_msg, user_msg])
        messages = LLMConvertibleEvent.events_to_messages(events)
        assert len(messages) == 2
        assert messages[0].role == "assistant"
        assert messages[1].role == "user"

    def test_single_action_event(self):
        """Test conversion of single ActionEvent."""
        action_event = create_action_event(
            thought_text="I need to run a command",
            tool_name="terminal",
            tool_call_id="call_123",
            llm_response_id="response_1",
            action_args={"command": "ls -la"},
        )

        events = cast(list[LLMConvertibleEvent], [action_event])
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert len(messages[0].content) == 1
        assert isinstance(messages[0].content[0], TextContent)
        assert messages[0].content[0].text == "I need to run a command"
        assert messages[0].tool_calls is not None
        assert len(messages[0].tool_calls) == 1
        assert messages[0].tool_calls[0].id == "call_123"
        assert messages[0].tool_calls[0].name == "terminal"

    def test_parallel_function_calling_same_response_id(self):
        """Test parallel function calling with multiple ActionEvents having same ID.

        This simulates the scenario from LiteLLM docs where the model makes multiple
        function calls in parallel (e.g., getting weather for multiple cities).
        """
        # Create multiple ActionEvents with same llm_response_id
        # First event has thought, others should have empty thought
        action1 = create_action_event(
            thought_text="I need to get weather for multiple cities",
            tool_name="get_current_weather",
            tool_call_id="call_SF",
            llm_response_id="response_parallel",
            action_args={"location": "San Francisco", "unit": "celsius"},
        )

        action2 = ActionEvent(
            source="agent",
            thought=[],  # Empty thought for subsequent actions in parallel call
            action=EventsToMessagesMockAction(command="test"),
            tool_name="get_current_weather",
            tool_call_id="call_Tokyo",
            tool_call=create_tool_call(
                "call_Tokyo",
                "get_current_weather",
                {"location": "Tokyo", "unit": "celsius"},
            ),
            llm_response_id="response_parallel",
        )

        action3 = ActionEvent(
            source="agent",
            thought=[],  # Empty thought for subsequent actions in parallel call
            action=EventsToMessagesMockAction(command="test"),
            tool_name="get_current_weather",
            tool_call_id="call_Paris",
            tool_call=create_tool_call(
                "call_Paris",
                "get_current_weather",
                {"location": "Paris", "unit": "celsius"},
            ),
            llm_response_id="response_parallel",
        )

        events = cast(list[LLMConvertibleEvent], [action1, action2, action3])
        messages = LLMConvertibleEvent.events_to_messages(events)

        # Should combine into single assistant message with multiple tool_calls
        assert len(messages) == 1
        assert messages[0].role == "assistant"

        # Content should come from first event's thought
        assert len(messages[0].content) == 1
        assert isinstance(messages[0].content[0], TextContent)
        assert (
            messages[0].content[0].text == "I need to get weather for multiple cities"
        )

        # Should have all three tool calls
        tool_calls = messages[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 3

        # Verify tool call details
        tool_call_ids = [tc.id for tc in tool_calls]
        assert "call_SF" in tool_call_ids
        assert "call_Tokyo" in tool_call_ids
        assert "call_Paris" in tool_call_ids

        # All should be weather function calls
        for tool_call in tool_calls:
            assert tool_call.name == "get_current_weather"

    def test_parallel_batch_preserves_first_event_reasoning(self):
        """The combined message keeps the first event's reasoning state.

        ThinkingBlock's own docstring requires these blocks be preserved and
        passed back to the API for tool use scenarios — so a batched message
        must carry them exactly like a singleton does.
        """
        first = ActionEvent(
            source="agent",
            thought=[TextContent(text="batching two calls")],
            reasoning_content="step-by-step reasoning",
            thinking_blocks=[ThinkingBlock(thinking="let me think", signature="sig-1")],
            action=EventsToMessagesMockAction(command="one"),
            tool_name="terminal",
            tool_call_id="call_1",
            tool_call=create_tool_call("call_1", "terminal", {"command": "one"}),
            llm_response_id="response_reasoning",
        )
        second = ActionEvent(
            source="agent",
            thought=[],
            action=EventsToMessagesMockAction(command="two"),
            tool_name="terminal",
            tool_call_id="call_2",
            tool_call=create_tool_call("call_2", "terminal", {"command": "two"}),
            llm_response_id="response_reasoning",
        )

        events = [first, second]
        messages = LLMConvertibleEvent.events_to_messages(events)  # type: ignore

        assert len(messages) == 1
        combined = messages[0]
        assert combined.reasoning_content == "step-by-step reasoning"
        assert len(combined.thinking_blocks) == 1
        assert combined.thinking_blocks[0].thinking == "let me think"  # type: ignore

    def test_parallel_batch_preserves_responses_reasoning_item(self):
        """A singleton keeps its responses reasoning item; a batch must too."""
        first = ActionEvent(
            source="agent",
            thought=[TextContent(text="batching two calls")],
            responses_reasoning_item=ReasoningItemModel(
                id="rs_1", summary=["reasoned about the batch"]
            ),
            action=EventsToMessagesMockAction(command="one"),
            tool_name="terminal",
            tool_call_id="call_1",
            tool_call=create_tool_call("call_1", "terminal", {"command": "one"}),
            llm_response_id="response_reasoning_item",
        )
        second = ActionEvent(
            source="agent",
            thought=[],
            action=EventsToMessagesMockAction(command="two"),
            tool_name="terminal",
            tool_call_id="call_2",
            tool_call=create_tool_call("call_2", "terminal", {"command": "two"}),
            llm_response_id="response_reasoning_item",
        )

        events = [first, second]
        messages = LLMConvertibleEvent.events_to_messages(events)  # type: ignore

        assert len(messages) == 1
        combined = messages[0]
        assert combined.responses_reasoning_item is not None
        assert combined.responses_reasoning_item.id == "rs_1"

    def test_multiple_separate_action_events(self):
        """Test multiple ActionEvents with different response_ids (separate calls)."""
        action1 = create_action_event(
            thought_text="First command",
            tool_name="terminal",
            tool_call_id="call_1",
            llm_response_id="response_1",
            action_args={"command": "ls"},
        )

        action2 = create_action_event(
            thought_text="Second command",
            tool_name="terminal",
            tool_call_id="call_2",
            llm_response_id="response_2",
            action_args={"command": "pwd"},
        )

        events = [action1, action2]
        messages = LLMConvertibleEvent.events_to_messages(events)  # type: ignore

        # Should create separate messages for different response IDs
        assert len(messages) == 2

        assert messages[0].role == "assistant"
        assert messages[0].content[0].text == "First command"  # type: ignore
        assert messages[0].tool_calls[0].id == "call_1"  # type: ignore

        assert messages[1].role == "assistant"
        assert messages[1].content[0].text == "Second command"  # type: ignore
        assert messages[1].tool_calls[0].id == "call_2"  # type: ignore

    def test_mixed_event_types(self):
        """Test conversion of mixed event types in sequence."""
        # System prompt
        system_event = SystemPromptEvent(
            system_prompt=TextContent(text="You are a helpful assistant."), tools=[]
        )

        # User message
        user_message = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="What's the weather like?")]
            ),
        )

        # Action event
        action_event = create_action_event(
            thought_text="I'll check the weather",
            tool_name="get_weather",
            tool_call_id="call_weather",
            llm_response_id="response_weather",
            action_args={"location": "current"},
        )

        # Observation event
        observation_event = ObservationEvent(
            source="environment",
            observation=EventsToMessagesMockObservation(result="Sunny, 72°F"),
            action_id="action_123",
            tool_name="get_weather",
            tool_call_id="call_weather",
        )

        events = [system_event, user_message, action_event, observation_event]
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert len(messages) == 4

        # System message
        assert messages[0].role == "system"
        assert messages[0].content[0].text == "You are a helpful assistant."  # type: ignore

        # User message
        assert messages[1].role == "user"
        assert messages[1].content[0].text == "What's the weather like?"  # type: ignore

        # Assistant message with tool call
        assert messages[2].role == "assistant"
        assert messages[2].content[0].text == "I'll check the weather"  # type: ignore
        assert messages[2].tool_calls is not None
        assert messages[2].tool_calls[0].id == "call_weather"  # type: ignore

        # Tool response
        assert messages[3].role == "tool"
        assert messages[3].content[0].text == "Sunny, 72°F"  # type: ignore
        assert messages[3].tool_call_id == "call_weather"
        assert messages[3].name == "get_weather"

    def test_agent_error_event(self):
        """Test conversion of AgentErrorEvent."""
        error_event = AgentErrorEvent(
            error="Command failed with exit code 1",
            tool_call_id="call_err",
            tool_name="terminal",
        )

        events = [error_event]
        messages = LLMConvertibleEvent.events_to_messages(events)  # type: ignore

        assert len(messages) == 1
        assert messages[0].role == "tool"
        assert messages[0].content[0].text == "Command failed with exit code 1"  # type: ignore

    def test_complex_parallel_and_sequential_mix(self):
        """Test complex scenario with both parallel and sequential function calls."""
        # First: User message
        user_msg = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[
                    TextContent(text="Get weather for SF and NYC, then list files")
                ],
            ),
        )

        # Second: Parallel weather calls (same response_id)
        weather_sf = create_action_event(
            thought_text="I'll get weather for both cities in parallel",
            tool_name="get_weather",
            tool_call_id="call_sf_weather",
            llm_response_id="parallel_weather",
            action_args={"location": "San Francisco"},
        )

        weather_nyc = ActionEvent(
            source="agent",
            thought=[],  # Empty for parallel call
            action=EventsToMessagesMockAction(command="test"),
            tool_name="get_weather",
            tool_call_id="call_nyc_weather",
            tool_call=create_tool_call(
                "call_nyc_weather", "get_weather", {"location": "New York"}
            ),
            llm_response_id="parallel_weather",
        )

        # Third: Weather observations
        obs_sf = ObservationEvent(
            source="environment",
            observation=EventsToMessagesMockObservation(result="SF: Sunny, 65°F"),
            action_id="action_sf",
            tool_name="get_weather",
            tool_call_id="call_sf_weather",
        )

        obs_nyc = ObservationEvent(
            source="environment",
            observation=EventsToMessagesMockObservation(result="NYC: Cloudy, 45°F"),
            action_id="action_nyc",
            tool_name="get_weather",
            tool_call_id="call_nyc_weather",
        )

        # Fourth: Separate file listing call (different response_id)
        list_files = create_action_event(
            thought_text="Now I'll list the files",
            tool_name="terminal",
            tool_call_id="call_ls",
            llm_response_id="list_files_response",
            action_args={"command": "ls -la"},
        )

        events = [user_msg, weather_sf, weather_nyc, obs_sf, obs_nyc, list_files]
        messages = LLMConvertibleEvent.events_to_messages(events)

        assert len(messages) == 5

        # User message
        assert messages[0].role == "user"

        # Combined parallel weather calls
        assert messages[1].role == "assistant"
        assert (
            messages[1].content[0].text  # type: ignore
            == "I'll get weather for both cities in parallel"
        )
        assert len(messages[1].tool_calls) == 2  # type: ignore

        # Weather observations
        assert messages[2].role == "tool"
        assert messages[2].tool_call_id == "call_sf_weather"
        assert messages[3].role == "tool"
        assert messages[3].tool_call_id == "call_nyc_weather"

        # Separate file listing call
        assert messages[4].role == "assistant"
        assert messages[4].content[0].text == "Now I'll list the files"  # type: ignore
        assert len(messages[4].tool_calls) == 1  # type: ignore
        assert messages[4].tool_calls[0].id == "call_ls"  # type: ignore

    def test_assertion_error_for_non_empty_thought_in_parallel_calls(self):
        """Test assertion error for non-empty thought in subsequent parallel calls."""
        action1 = create_action_event(
            thought_text="First thought",
            tool_name="get_weather",
            tool_call_id="call_1",
            llm_response_id="same_response",
            action_args={"location": "SF"},
        )

        # This should cause assertion error - non-empty thought in subsequent call
        action2 = ActionEvent(
            source="agent",
            thought=[TextContent(text="This should not be here!")],  # Non-empty thought
            action=EventsToMessagesMockAction(command="test"),
            tool_name="get_weather",
            tool_call_id="call_2",
            tool_call=create_tool_call("call_2", "get_weather", {"location": "NYC"}),
            llm_response_id="same_response",
        )

        events = [action1, action2]

        with pytest.raises(
            AssertionError,
            match="Expected empty thought for multi-action events after the first one",
        ):
            LLMConvertibleEvent.events_to_messages(events)  # type: ignore

    def test_action_event_with_none_action_round_trip_and_observation_match(self):
        """Test ActionEvent with action=None round trip and observation match."""
        thought = [TextContent(text="thinking...")]
        tc = create_tool_call("call_ne", "missing_tool", {"x": 1})
        action_event = ActionEvent(
            source="agent",
            thought=thought,
            tool_call=tc,
            tool_name=tc.name,
            tool_call_id=tc.id,
            llm_response_id="resp_events_1",
            action=None,
        )

        # Convert to messages and ensure assistant message has single tool_call
        messages = LLMConvertibleEvent.events_to_messages([action_event])
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].tool_calls is not None and len(messages[0].tool_calls) == 1
        assert messages[0].tool_calls[0].id == "call_ne"
        assert messages[0].tool_calls[0].name == "missing_tool"

        # Simulate an AgentErrorEvent that carries the same tool_call_id
        err = AgentErrorEvent(
            error="not found",
            tool_call_id="call_ne",
            tool_name="missing_tool",
        )

        msgs = LLMConvertibleEvent.events_to_messages([action_event, err])
        # Should produce two messages: assistant tool call + tool error
        assert len(msgs) == 2
        assert msgs[0].role == "assistant"
        assert msgs[1].role == "tool"
        assert msgs[1].tool_call_id == "call_ne"
