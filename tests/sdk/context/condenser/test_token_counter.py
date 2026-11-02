"""Tests for the incremental token counter used by the condenser."""

from typing import Literal, cast
from unittest.mock import MagicMock

import pytest

from openhands.sdk.context.condenser.token_counter import IncrementalTokenCounter
from openhands.sdk.event.base import LLMConvertibleEvent, SourceType
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.event.llm_convertible.system import SystemPromptEvent
from openhands.sdk.llm import (
    LLM,
    ImageContent,
    Message,
    MessageToolCall,
    TextContent,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.builtins.finish import FinishTool


def _message_event(
    content: str,
    role: Literal["user", "system", "assistant", "tool"] = "user",
    source: SourceType = "user",
) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role=role, content=[TextContent(text=content)]),
        source=source,
    )


def _action_event(
    tool_call_id: str, llm_response_id: str, *, thought: bool = True
) -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text="think")] if thought else [],
        action=MCPToolAction(data={}),
        tool_name="test_tool",
        tool_call_id=tool_call_id,
        tool_call=MessageToolCall(
            id=tool_call_id,
            name="test_tool",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id=llm_response_id,
        source="agent",
    )


def _full_count(events, llm: LLM) -> int:
    from openhands.sdk.context.condenser.utils import get_total_token_count

    return get_total_token_count(events, llm)


def _observation_event(tool_call_id: str, content: str = "Success") -> ObservationEvent:
    return ObservationEvent(
        observation=MCPToolObservation.from_text(text=content, tool_name="test_tool"),
        tool_name="test_tool",
        tool_call_id=tool_call_id,
        action_id="action_event_id",
        source="environment",
    )


@pytest.fixture
def llm() -> LLM:
    # LiteLLM counts tokens offline from the model name; no network is used.
    return LLM(model="gpt-4o", api_key="test")


class TestIncrementalTokenCounterMatchesFullCount:
    def test_empty(self, llm: LLM):
        assert IncrementalTokenCounter(llm).count([]) == _full_count([], llm)

    def test_growing_view_matches_full_count(self, llm: LLM):
        events = [_message_event("system prompt", "system", "agent")]
        events.append(_message_event("hello"))
        counter = IncrementalTokenCounter(llm)

        for length in range(1, len(events) + 1):
            assert counter.count(events[:length]) == _full_count(events[:length], llm)

        events.append(_message_event("world"))
        assert counter.count(events) == _full_count(events, llm)

    def test_tool_calls_and_tool_results(self, llm: LLM):
        events: list = [_message_event("system prompt", "system", "agent")]
        events.append(_message_event("run something"))
        events.append(_action_event("tc1", "r1"))
        events.append(_observation_event("tc1", "output text"))
        counter = IncrementalTokenCounter(llm)

        for length in range(1, len(events) + 1):
            assert counter.count(events[:length]) == _full_count(events[:length], llm)

    def test_with_and_without_tools(self, llm: LLM):
        tools = cast(list[ToolDefinition], list(FinishTool.create()))
        events = [
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="system"),
                tools=tools,
            ),
            _message_event("hello"),
        ]
        counter = IncrementalTokenCounter(llm)
        # With tools (the SystemPromptEvent supplies them)...
        assert counter.count(events) == _full_count(events, llm)
        # ...and without tools (no SystemPromptEvent in the sequence).
        assert counter.count(events[1:]) == _full_count(events[1:], llm)

    def test_image_content(self, llm: LLM):
        image_message = MessageEvent(
            llm_message=Message(
                role="user",
                content=[
                    TextContent(text="look at this"),
                    ImageContent(
                        image_urls=["data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="]
                    ),
                ],
            ),
            source="user",
        )
        events = [_message_event("system prompt", "system", "agent"), image_message]
        counter = IncrementalTokenCounter(llm)
        assert counter.count(events) == _full_count(events, llm)
        events.append(_message_event("follow up"))
        assert counter.count(events) == _full_count(events, llm)

    def test_tools_are_counted(self, llm: LLM):
        tools = cast(list[ToolDefinition], list(FinishTool.create()))
        events = [
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="system"),
                tools=tools,
            ),
            _message_event("hello"),
        ]
        counter = IncrementalTokenCounter(llm)
        assert counter.count(events) == _full_count(events, llm)
        events.append(_message_event("again"))
        assert counter.count(events) == _full_count(events, llm)


class TestEarlierMessageMutation:
    """The event-to-message projection is not append-only.

    Appending an ActionEvent that shares an ``llm_response_id`` appends a
    ``tool_call`` to the *earlier* assistant message, and two adjacent plain
    user messages coalesce. A positional cache would silently go stale; the
    content-keyed cache must produce the same total as a full recount.
    """

    def test_appended_tool_call_to_earlier_message(self, llm: LLM):
        events: list = [_message_event("system prompt", "system", "agent")]
        events.append(_message_event("run something"))
        events.append(_action_event("tc1", "r1"))
        counter = IncrementalTokenCounter(llm)

        before = counter.count(events)
        assert before == _full_count(events, llm)

        # Same llm_response_id: this merges into the existing assistant message,
        # growing its tool_calls from 1 to 2 rather than adding a message.
        events.append(_action_event("tc2", "r1", thought=False))
        assert counter.count(events) == _full_count(events, llm)

    def test_coalescing_user_messages(self, llm: LLM):
        events: list = [_message_event("system prompt", "system", "agent")]
        events.append(_message_event("hello"))
        counter = IncrementalTokenCounter(llm)
        assert counter.count(events) == _full_count(events, llm)

        events.append(_message_event("there"))
        assert counter.count(events) == _full_count(events, llm)


class TestShrinkingView:
    def test_shrink_after_condensation_matches_full_count(self, llm: LLM):
        events = [_message_event("system prompt", "system", "agent")]
        events += [_message_event(f"message {i}") for i in range(6)]
        counter = IncrementalTokenCounter(llm)
        assert counter.count(events) == _full_count(events, llm)

        # The view shrinks (events forgotten by condensation).
        shrunk = [events[0], events[-1]]
        assert counter.count(shrunk) == _full_count(shrunk, llm)

        # And grows again afterwards.
        shrunk.append(_message_event("after"))
        assert counter.count(shrunk) == _full_count(shrunk, llm)


class TestCacheReuse:
    def test_unchanged_messages_are_not_recounted(self):
        """Only messages that changed should be re-tokenised."""
        llm = MagicMock(spec=LLM)
        calls: list[int] = []

        def count(messages, **_kwargs):
            calls.append(len(messages))
            return sum(len(c.text) for m in messages for c in m.content)

        llm.get_token_count.side_effect = count
        counter = IncrementalTokenCounter(llm)

        # Distinct roles so the two events project to two messages (adjacent
        # user messages would coalesce into one, leaving nothing to cache).
        events = [_message_event("a", "system", "agent"), _message_event("bb")]
        counter.count(events)
        first_step = len(calls)

        # Re-count the identical view: no new per-message counts should run.
        counter.count(events)
        assert len(calls) == first_step

        # Appending one message counts exactly the new message once.
        events.append(_message_event("ccc", "assistant", "agent"))
        counter.count(events)
        assert calls[-1] == 1


class TestNonAdditiveCountingPath:
    def test_falls_back_when_per_message_counts_do_not_add_up(self):
        """A non-additive counting path must fall back to a full recount."""
        llm = MagicMock(spec=LLM)

        def count(messages, **_kwargs):
            # Deliberately non-additive: the whole list counts as one unit.
            return 100 if messages else 0

        llm.get_token_count.side_effect = count
        counter = IncrementalTokenCounter(llm)

        # Distinct roles so the projection does not coalesce them into one turn.
        events = [_message_event("a", "system", "agent"), _message_event("b")]
        assert counter.count(events) == 100
        assert counter._additive is False
        # Subsequent calls keep returning the full recount.
        events.append(_message_event("c", "assistant", "agent"))
        assert counter.count(events) == 100

    def test_one_message_input_does_not_establish_additivity(self):
        """A single message must not be taken as proof of additivity.

        With one message the cached sum is trivially equal to the full count, so
        an empirical check there proves nothing. The counter must stay unverified
        (returning exact recounts) until it sees a multi-message input, then
        verify against it.
        """
        llm = MagicMock(spec=LLM)

        def count(messages, **_kwargs):
            # Additive over a single message, but not over the whole list: this
            # mirrors prompt-mocked tool calling, whose first-user example is
            # positional. One message -> 10; more -> 10 per message minus 5.
            if len(messages) <= 1:
                return 10 * len(messages)
            return 10 * len(messages) - 5

        llm.get_token_count.side_effect = count
        counter = IncrementalTokenCounter(llm)

        events = [
            _message_event("system", "system", "agent"),
            _message_event("first user turn"),
            _message_event("reply", "assistant", "agent"),
        ]

        # One message: exact recount, and additivity stays unverified.
        assert counter.count(events[:1]) == 10
        assert counter._additive is None

        # First multi-message input: the check now runs and rejects the path.
        assert counter.count(events) == 25
        assert counter._additive is False

        # And it keeps returning exact recounts afterwards.
        events.append(_message_event("second user turn"))
        assert counter.count(events) == 35


def _non_native_events(tools) -> list:
    return [
        SystemPromptEvent(
            source="agent",
            system_prompt=TextContent(text="You are an agent."),
            tools=tools,
        ),
        _message_event("first user turn"),
        _message_event("assistant reply", "assistant", "agent"),
        _message_event("second user turn"),
    ]


@pytest.fixture
def non_native_llm() -> LLM:
    return LLM(model="gpt-4o", api_key="test", native_tool_calling=False)


def test_non_native_tool_calling_matches_full_count_at_every_step(
    non_native_llm: LLM,
):
    """Regression with a real non-native tool-calling LLM (no mocks).

    Prompt-mocked tool calling attaches its in-context example positionally, so
    per-message counts do not add up to the full-history count. The counter must
    still return the same total as ``get_total_token_count`` at every step.
    """
    tools = cast(list[ToolDefinition], list(FinishTool.create()))
    events = _non_native_events(tools)
    counter = IncrementalTokenCounter(non_native_llm)

    for length in range(1, len(events) + 1):
        view = events[:length]
        assert counter.count(view) == _full_count(view, non_native_llm)


def test_cache_is_disabled_for_non_native_tool_calling(non_native_llm: LLM):
    tools = cast(list[ToolDefinition], list(FinishTool.create()))
    counter = IncrementalTokenCounter(non_native_llm)
    counter.count(_non_native_events(tools))
    assert counter._additive is False


def test_native_tool_calling_still_uses_the_cache():
    llm = LLM(model="gpt-4o", api_key="test")
    tools = cast(list[ToolDefinition], list(FinishTool.create()))
    events = _non_native_events(tools)
    counter = IncrementalTokenCounter(llm)

    for length in range(1, len(events) + 1):
        view = events[:length]
        assert counter.count(view) == _full_count(view, llm)
    assert counter._additive is True


def test_counter_tracks_llm_tool_configuration():
    """Changing the tool schema must not reuse a stale additivity constant."""
    llm = MagicMock(spec=LLM)
    seen: list[tuple] = []

    def count(messages, tools=None, add_security_risk_prediction=False):
        seen.append((tools, add_security_risk_prediction))
        return 10 * len(messages) + (len(tools) if tools else 0)

    llm.get_token_count.side_effect = count
    counter = IncrementalTokenCounter(llm)

    tools_a = cast(list[ToolDefinition], list(FinishTool.create()))
    tools_b = cast(list[ToolDefinition], list(FinishTool.create()))
    with_a = [
        SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="s"), tools=tools_a
        ),
        _message_event("hi"),
    ]
    with_b = [
        SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="s"), tools=tools_b
        ),
        _message_event("hi"),
    ]
    counter.count(with_a)
    # A different tools object invalidates the cache, so per-message counts run
    # again rather than reusing the previous constant.
    before = len(seen)
    counter.count(with_b)
    assert len(seen) > before


def test_events_to_messages_returns_fresh_objects():
    """Guard the assumption behind ``==`` comparison in the cache."""
    events: list = [_message_event("system prompt", "system", "agent")]
    events.append(_message_event("run"))
    events.append(_action_event("tc1", "r1"))
    first = LLMConvertibleEvent.events_to_messages(list(events))
    second = LLMConvertibleEvent.events_to_messages(list(events))
    # Equal by value (so the cache can compare) but distinct objects.
    assert first == second
    assert all(a is not b for a, b in zip(first, second))
