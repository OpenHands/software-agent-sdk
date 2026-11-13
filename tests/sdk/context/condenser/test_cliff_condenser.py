"""Tests for CliffCondenser."""

from collections.abc import Sequence
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from openhands.sdk.context.condenser import CliffCondenser
from openhands.sdk.context.condenser.base import (
    CondensationRequirement,
    NoCondensationAvailableException,
)
from openhands.sdk.context.condenser.cliff_condenser import SUMMARY_HEADER
from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import CondensationRequest, CondensationSummaryEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import (
    LLM,
    Message,
    MessageToolCall,
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


# --- builders -----------------------------------------------------------


def system_event(text: str = "You are a helpful agent.") -> SystemPromptEvent:
    return SystemPromptEvent(system_prompt=TextContent(text=text), tools=[])


def user_event(text: str) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=text)]),
        source="user",
    )


def agent_message_event(
    text: str,
    reasoning_content: str | None = None,
    thinking_blocks: Sequence[ThinkingBlock | RedactedThinkingBlock] | None = None,
) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(
            role="assistant",
            content=[TextContent(text=text)],
            reasoning_content=reasoning_content,
            thinking_blocks=list(thinking_blocks or []),
        ),
        source="agent",
    )


def action_event(
    response_id: str,
    call_id: str,
    thought: str = "Test thought",
    arguments: str = "{}",
    tool_name: str = "test_tool",
    reasoning_content: str | None = None,
    thinking_blocks: Sequence[ThinkingBlock | RedactedThinkingBlock] | None = None,
) -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text=thought)] if thought else [],
        reasoning_content=reasoning_content,
        thinking_blocks=list(thinking_blocks or []),
        action=MCPToolAction(data={}),
        tool_name=tool_name,
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id, name=tool_name, arguments=arguments, origin="completion"
        ),
        llm_response_id=response_id,
        source="agent",
    )


def observation_event(
    call_id: str, text: str = "Success", tool_name: str = "test_tool"
) -> ObservationEvent:
    return ObservationEvent(
        observation=MCPToolObservation.from_text(text=text, tool_name=tool_name),
        tool_name=tool_name,
        tool_call_id=call_id,
        action_id=f"action-{call_id}",
        source="environment",
    )


def turn(index: int, result: str = "Success") -> list[Event]:
    """One action plus its observation."""
    return [
        action_event(f"resp-{index}", f"call-{index}", thought=f"Thought {index}"),
        observation_event(f"call-{index}", text=result),
    ]


def conversation(n_turns: int, result: str = "Success") -> list[Event]:
    events: list[Event] = [system_event(), user_event("Do the thing.")]
    for i in range(n_turns):
        events.extend(turn(i, result=result))
    return events


def mock_llm(limit: int | None = None, tokens: int = 0) -> LLM:
    llm = MagicMock(spec=LLM)
    llm.effective_max_input_tokens = limit
    llm.get_token_count = MagicMock(return_value=tokens)
    return llm


def summary_of(view: View) -> str:
    summaries = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]
    assert len(summaries) == 1, f"expected exactly one summary, got {len(summaries)}"
    return summaries[0].summary


def condense_once(condenser: CliffCondenser, events: list[Event]) -> list[Event]:
    """Run one condensation and return the extended event log."""
    condensation = condenser.get_condensation(View.from_events(events))
    return [*events, condensation]


# --- structure ----------------------------------------------------------


def test_summary_replaces_middle_and_keeps_head_and_recent() -> None:
    condenser = CliffCondenser(keep_recent=2)
    events = conversation(6)

    view = View.from_events(condense_once(condenser, events))

    # head: system prompt + opening user message, then the summary.
    assert isinstance(view.events[0], SystemPromptEvent)
    assert isinstance(view.events[1], MessageEvent)
    assert isinstance(view.events[2], CondensationSummaryEvent)

    # tail: the last two turns, verbatim.
    tail = view.events[3:]
    assert len(tail) == 4
    assert [type(e) for e in tail] == [
        ActionEvent,
        ObservationEvent,
        ActionEvent,
        ObservationEvent,
    ]
    assert [e.llm_response_id for e in tail if isinstance(e, ActionEvent)] == [
        "resp-4",
        "resp-5",
    ]


def test_summary_starts_with_header_and_holds_digested_turns() -> None:
    condenser = CliffCondenser(keep_recent=2)
    summary = summary_of(View.from_events(condense_once(condenser, conversation(6))))

    assert summary.startswith(SUMMARY_HEADER)
    # Turns 0-3 were digested; 4-5 stayed verbatim.
    for i in range(4):
        assert f"Thought {i}" in summary
    assert "Thought 4" not in summary
    assert "[test_tool] {}" in summary
    assert "result: Success" in summary


def test_too_few_turns_raises() -> None:
    condenser = CliffCondenser(keep_recent=3)
    with pytest.raises(NoCondensationAvailableException):
        condenser.get_condensation(View.from_events(conversation(3)))


def test_empty_history_raises() -> None:
    condenser = CliffCondenser(keep_recent=1)
    with pytest.raises(NoCondensationAvailableException):
        condenser.get_condensation(
            View.from_events([system_event(), user_event("Hi.")])
        )


def test_parallel_tool_calls_are_one_turn() -> None:
    """Two actions sharing an llm_response_id open a single turn."""
    events: list[Event] = [system_event(), user_event("Do the thing.")]
    for i in range(4):
        events.append(action_event(f"resp-{i}", f"call-{i}a"))
        events.append(action_event(f"resp-{i}", f"call-{i}b"))
        events.append(observation_event(f"call-{i}a"))
        events.append(observation_event(f"call-{i}b"))

    view = View.from_events(condense_once(CliffCondenser(keep_recent=2), events))

    kept = [e for e in view.events if isinstance(e, ActionEvent)]
    assert [e.llm_response_id for e in kept] == [
        "resp-2",
        "resp-2",
        "resp-3",
        "resp-3",
    ]


def test_agent_message_without_tool_call_starts_a_turn() -> None:
    events: list[Event] = [system_event(), user_event("Hi.")]
    for i in range(4):
        events.append(agent_message_event(f"Reply {i}"))

    view = View.from_events(condense_once(CliffCondenser(keep_recent=2), events))

    kept = [
        c.text
        for e in view.events
        if isinstance(e, MessageEvent) and e.source == "agent"
        for c in e.llm_message.content
        if isinstance(c, TextContent)
    ]
    assert kept == ["Reply 2", "Reply 3"]
    assert "Reply 1" in summary_of(view)


# --- content rules ------------------------------------------------------


def test_long_results_are_dropped_short_ones_kept() -> None:
    condenser = CliffCondenser(keep_recent=1, result_max_chars=20)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.extend(turn(0, result="short"))
    events.extend(turn(1, result="x" * 500))
    events.extend(turn(2))

    summary = summary_of(View.from_events(condense_once(condenser, events)))

    assert "result: short" in summary
    assert "x" * 50 not in summary
    # The call that produced the dropped result is still named.
    assert summary.count("[test_tool]") == 2


def test_tool_call_arguments_are_truncated() -> None:
    condenser = CliffCondenser(keep_recent=1, cmd_max_chars=10)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(action_event("resp-0", "call-0", arguments='{"path": "/very/long"}'))
    events.append(observation_event("call-0"))
    events.extend(turn(1))

    summary = summary_of(View.from_events(condense_once(condenser, events)))

    assert '[test_tool] {"path": "...' in summary
    assert "/very/long" not in summary


def test_assistant_text_is_kept_whole_by_default() -> None:
    condenser = CliffCondenser(keep_recent=1)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(action_event("resp-0", "call-0", thought="T" * 2000))
    events.append(observation_event("call-0"))
    events.extend(turn(1))

    assert "T" * 2000 in summary_of(View.from_events(condense_once(condenser, events)))


def test_assistant_text_respects_its_cap() -> None:
    condenser = CliffCondenser(keep_recent=1, thought_max_chars=10)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(action_event("resp-0", "call-0", thought="T" * 2000))
    events.append(observation_event("call-0"))
    events.extend(turn(1))

    summary = summary_of(View.from_events(condense_once(condenser, events)))
    assert "assistant: " + "T" * 10 + "..." in summary


def test_human_messages_are_kept_verbatim() -> None:
    condenser = CliffCondenser(keep_recent=1)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.extend(turn(0))
    events.append(user_event("Also check the logs."))
    events.extend(turn(1))
    events.extend(turn(2))

    assert "user: Also check the logs." in summary_of(
        View.from_events(condense_once(condenser, events))
    )


def test_agent_errors_are_digested_as_results() -> None:
    condenser = CliffCondenser(keep_recent=1)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(action_event("resp-0", "call-0"))
    events.append(
        AgentErrorEvent(
            error="tool blew up",
            tool_name="test_tool",
            tool_call_id="call-0",
        )
    )
    events.extend(turn(1))
    events.extend(turn(2))

    assert "result: tool blew up" in summary_of(
        View.from_events(condense_once(condenser, events))
    )


# --- reasoning ----------------------------------------------------------


def test_thinking_blocks_win_over_derived_reasoning_content() -> None:
    """litellm derives reasoning_content from the blocks, so reading both repeats it."""
    condenser = CliffCondenser(keep_recent=1)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(
        action_event(
            "resp-0",
            "call-0",
            reasoning_content="first part.second part.",
            thinking_blocks=[
                ThinkingBlock(thinking="first part."),
                ThinkingBlock(thinking="second part."),
            ],
        )
    )
    events.append(observation_event("call-0"))
    events.extend(turn(1))

    summary = summary_of(View.from_events(condense_once(condenser, events)))

    assert summary.count("first part.") == 1
    assert summary.count("second part.") == 1
    assert "thinking: first part.\nsecond part." in summary


def test_reasoning_content_is_the_fallback_without_blocks() -> None:
    condenser = CliffCondenser(keep_recent=1)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(action_event("resp-0", "call-0", reasoning_content="plain thoughts"))
    events.append(observation_event("call-0"))
    events.extend(turn(1))

    assert "thinking: plain thoughts" in summary_of(
        View.from_events(condense_once(condenser, events))
    )


def test_redacted_blocks_contribute_nothing() -> None:
    condenser = CliffCondenser(keep_recent=1)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(
        action_event(
            "resp-0",
            "call-0",
            thinking_blocks=[RedactedThinkingBlock(data="opaque")],
        )
    )
    events.append(observation_event("call-0"))
    events.extend(turn(1))

    summary = summary_of(View.from_events(condense_once(condenser, events)))
    assert "thinking:" not in summary
    assert "opaque" not in summary


def test_keep_thinking_false_drops_reasoning() -> None:
    condenser = CliffCondenser(keep_recent=1, keep_thinking=False)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(
        action_event(
            "resp-0", "call-0", thinking_blocks=[ThinkingBlock(thinking="secret")]
        )
    )
    events.append(observation_event("call-0"))
    events.extend(turn(1))

    summary = summary_of(View.from_events(condense_once(condenser, events)))
    assert "thinking:" not in summary
    assert "secret" not in summary


def test_agent_message_reasoning_is_digested() -> None:
    condenser = CliffCondenser(keep_recent=1)
    events: list[Event] = [system_event(), user_event("Go.")]
    events.append(
        agent_message_event(
            "Done.", thinking_blocks=[ThinkingBlock(thinking="deliberating")]
        )
    )
    events.append(agent_message_event("Next."))
    events.append(agent_message_event("Last."))

    summary = summary_of(View.from_events(condense_once(condenser, events)))
    assert "thinking: deliberating" in summary
    assert "assistant: Done." in summary


# --- re-condensation ----------------------------------------------------


def test_second_condensation_drops_the_first() -> None:
    """The earlier summary is discarded, not carried forward or merged."""
    condenser = CliffCondenser(keep_recent=2)
    events = condense_once(condenser, conversation(6))
    first = summary_of(View.from_events(events))
    assert "Thought 0" in first

    events.extend(turn(6))
    events.extend(turn(7))
    events = condense_once(condenser, events)
    second = summary_of(View.from_events(events))

    # Nothing of the first summary survives into the second.
    assert not second.startswith(first)
    assert "Thought 0" not in second
    assert "Thought 3" not in second
    # Only the turns that aged out since the previous condensation.
    assert "Thought 4" in second
    assert "Thought 5" in second
    assert "Thought 6" not in second  # still in the kept tail


def test_view_holds_exactly_one_summary_after_recondensation() -> None:
    condenser = CliffCondenser(keep_recent=2)
    events = condense_once(condenser, conversation(6))
    events.extend(turn(6))
    events.extend(turn(7))
    events = condense_once(condenser, events)

    view = View.from_events(events)
    assert sum(isinstance(e, CondensationSummaryEvent) for e in view.events) == 1


def test_one_summary_even_when_the_tail_starts_with_a_user_message() -> None:
    """A tool loop can push the cut past the next human message.

    A thinking block opens a loop that stays atomic across every response it
    spans, so the first legal cut can be the human message that follows it --
    leaving the next summary separated from the first agent event. The summary
    offset is a fixed index rather than that boundary, so the earlier summary
    still falls inside the condensed region.
    """
    condenser = CliffCondenser(keep_recent=3)
    events: list[Event] = [
        system_event(),
        user_event("Do the thing."),
        action_event(
            "resp-0", "call-0", thinking_blocks=[ThinkingBlock(thinking="planning")]
        ),
        observation_event("call-0"),
        *turn(1),
        *turn(2),
        user_event("Now do the other thing."),
        *turn(3),
        *turn(4),
    ]

    events = condense_once(condenser, events)
    tail = View.from_events(events).events[3:]
    assert isinstance(tail[0], MessageEvent) and tail[0].source == "user"

    events.extend(turn(5))
    events.extend(turn(6))
    view = View.from_events(condense_once(condenser, events))

    assert sum(isinstance(e, CondensationSummaryEvent) for e in view.events) == 1
    assert "Now do the other thing." in summary_of(view)


def test_summary_covers_only_newly_aged_turns() -> None:
    """A summary is a first-order digest of raw events, never a digest of a digest.

    One pass over the same history covers turns 0-5. Two incremental passes
    leave only 4-5, because the first summary falls away at the second
    condensation instead of being re-summarized into it.
    """
    condenser = CliffCondenser(keep_recent=2)

    incremental = condense_once(condenser, conversation(6))
    incremental.extend(turn(6))
    incremental.extend(turn(7))
    incremental = condense_once(condenser, incremental)
    incremental_summary = summary_of(View.from_events(incremental))

    one_pass = summary_of(View.from_events(condense_once(condenser, conversation(8))))

    assert incremental_summary != one_pass
    for i in range(4):
        assert f"Thought {i}" in one_pass
        assert f"Thought {i}" not in incremental_summary


def test_same_history_gives_the_same_summary() -> None:
    condenser = CliffCondenser(keep_recent=2)
    a = condenser.get_condensation(View.from_events(conversation(6)))
    b = condenser.get_condensation(View.from_events(conversation(6)))
    assert a.summary == b.summary


# --- triggering ---------------------------------------------------------


def test_handles_condensation_requests() -> None:
    assert CliffCondenser().handles_condensation_requests() is True


def test_explicit_request_is_hard() -> None:
    events: list[Event] = [*conversation(4), CondensationRequest()]
    view = View.from_events(events)
    assert (
        CliffCondenser().condensation_requirement(view, mock_llm())
        == CondensationRequirement.HARD
    )


def test_no_requirement_without_llm_or_budget() -> None:
    view = View.from_events(conversation(4))
    assert CliffCondenser().condensation_requirement(view, None) is None
    assert (
        CliffCondenser(max_tokens=None).condensation_requirement(
            view, mock_llm(None, 10**6)
        )
        is None
    )


def test_default_budget_applies_when_model_limit_is_unknown() -> None:
    view = View.from_events(conversation(4))
    condenser = CliffCondenser()
    assert condenser.max_tokens == 200_000
    assert (
        condenser.condensation_requirement(view, mock_llm(None, 10**6))
        == CondensationRequirement.SOFT
    )


def test_over_model_limit_is_hard() -> None:
    view = View.from_events(conversation(4))
    condenser = CliffCondenser()
    llm = mock_llm(limit=1000, tokens=1001)
    assert condenser.condensation_requirement(view, llm) == CondensationRequirement.HARD


def test_over_configured_budget_is_soft() -> None:
    view = View.from_events(conversation(4))
    condenser = CliffCondenser(max_tokens=500)
    llm = mock_llm(limit=1000, tokens=600)
    assert condenser.condensation_requirement(view, llm) == CondensationRequirement.SOFT


def test_under_budget_is_none() -> None:
    view = View.from_events(conversation(4))
    condenser = CliffCondenser(max_tokens=500)
    llm = mock_llm(limit=1000, tokens=100)
    assert condenser.condensation_requirement(view, llm) is None


def test_condense_returns_view_when_nothing_is_required() -> None:
    condenser = CliffCondenser(max_tokens=10**6)
    view = View.from_events(conversation(4))
    assert condenser.condense(view, mock_llm(limit=10**6, tokens=10)) is view


# --- hard context reset -------------------------------------------------


def test_hard_reset_keeps_only_head_and_summary() -> None:
    condenser = CliffCondenser(keep_recent=2)
    events = conversation(4)

    reset = condenser.hard_context_reset(View.from_events(events))
    assert reset is not None
    view = View.from_events([*events, reset])

    assert [type(e) for e in view.events] == [
        SystemPromptEvent,
        MessageEvent,
        CondensationSummaryEvent,
    ]
    for i in range(4):
        assert f"Thought {i}" in summary_of(view)


def test_hard_reset_ignores_keep_recent() -> None:
    """keep_recent is the only lever cliff has when no legal cut exists."""
    many = CliffCondenser(keep_recent=100)
    events = conversation(4)

    with pytest.raises(NoCondensationAvailableException):
        many.get_condensation(View.from_events(events))

    reset = many.hard_context_reset(View.from_events(events))
    assert reset is not None
    assert len(View.from_events([*events, reset]).events) == 3


def test_hard_reset_returns_none_with_nothing_to_forget() -> None:
    condenser = CliffCondenser()
    view = View.from_events([system_event(), user_event("Hi.")])
    assert condenser.hard_context_reset(view) is None


def test_hard_request_falls_back_to_reset() -> None:
    """condense() reaches the reset when a HARD trigger finds no legal cut."""
    condenser = CliffCondenser(keep_recent=100)
    events: list[Event] = [*conversation(4), CondensationRequest()]

    result = condenser.condense(View.from_events(events), mock_llm())

    assert not isinstance(result, View)
    assert len(View.from_events([*events, result]).events) == 3


# --- configuration ------------------------------------------------------


def test_keep_recent_must_be_at_least_one() -> None:
    """A view with no verbatim tail is the hard-reset path, not a setting."""
    with pytest.raises(ValidationError):
        CliffCondenser(keep_recent=0)


# --- serialization ------------------------------------------------------


def test_round_trips_through_json() -> None:
    condenser = CliffCondenser(keep_recent=4, max_tokens=120_000, result_max_chars=250)
    restored = CliffCondenser.model_validate_json(condenser.model_dump_json())
    assert restored == condenser
