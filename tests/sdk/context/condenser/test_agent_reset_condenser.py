import pytest

from openhands.sdk.context.condenser import (
    AgentResetCondenser,
    NoCondensationAvailableException,
)
from openhands.sdk.context.view import View
from openhands.sdk.event import (
    ActionEvent,
    Condensation,
    CondensationRequest,
    ContextWindowReminderEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, TextContent, ThinkingBlock
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.new_context import NewContextObservation


def test_reset_preserves_unseen_users_and_entire_tool_batch(reset_view: View):
    llm = TestLLM.from_messages([])
    result = AgentResetCondenser().condense(reset_view, llm)

    assert isinstance(result, Condensation)
    assert result.forgotten_event_ids == {"old-user", "boundary"}
    assert result.llm_response_id == "reset-response"
    assert result.summary is None
    assert llm.call_count == 0
    assert {event.id for event in result.apply(reset_view.events)} == {
        "system",
        "concurrent-user-1",
        "concurrent-user-2",
        "reset-action",
        "sibling-action",
        "reset-result",
        "sibling-result",
    }
    assert reset_view.pending_condensation_request is not None


@pytest.mark.parametrize("boundary", [None, "missing", "reset-action"])
def test_unknown_input_boundary_preserves_all_users(reset_view: View, boundary):
    observation = next(
        event for event in reset_view.events if event.id == "reset-result"
    )
    assert isinstance(observation, ObservationEvent)
    assert isinstance(observation.observation, NewContextObservation)
    replacement = observation.model_copy(
        update={
            "observation": observation.observation.model_copy(
                update={"input_event_id": boundary}
            )
        }
    )
    reset_view.events = [
        replacement if event.id == replacement.id else event
        for event in reset_view.events
    ]

    result = AgentResetCondenser().condense(reset_view)

    assert isinstance(result, Condensation)
    assert result.forgotten_event_ids == {"boundary"}


def test_reset_can_remove_all_already_processed_users(reset_view: View):
    reset_view.events = [
        event
        for event in reset_view.events
        if event.id not in {"concurrent-user-1", "concurrent-user-2"}
    ]

    result = AgentResetCondenser().condense(reset_view)

    assert isinstance(result, Condensation)
    assert result.forgotten_event_ids == {"old-user", "boundary"}


def test_reset_preserves_entire_thinking_loop(reset_view: View):
    sibling = next(event for event in reset_view.events if event.id == "sibling-action")
    assert isinstance(sibling, ActionEvent)
    sibling = sibling.model_copy(
        update={
            "llm_response_id": "earlier-response",
            "thinking_blocks": [ThinkingBlock(thinking="Plan the remaining work")],
        }
    )
    result = next(event for event in reset_view.events if event.id == "sibling-result")
    reset_view.events = [
        event
        for event in reset_view.events
        if event.id not in {"sibling-action", "sibling-result"}
    ]
    reset_view.events[5:5] = [sibling, result]

    condensation = AgentResetCondenser().condense(reset_view)

    assert isinstance(condensation, Condensation)
    assert condensation.forgotten_event_ids == {"old-user", "boundary"}


def test_reset_fails_when_protected_input_exceeds_capacity(reset_view: View):
    reset_view.events.insert(
        5,
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="New input " * 10_000)]
            ),
        ),
    )
    llm = TestLLM.from_messages([], max_input_tokens=16_384)

    with pytest.raises(NoCondensationAvailableException, match="protected input"):
        AgentResetCondenser().condense(reset_view, llm)
    assert llm.call_count == 0


def test_reminder_replays_once_per_changed_window(reset_view: View):
    condenser = AgentResetCondenser()
    llm = TestLLM.from_messages([], max_input_tokens=100_000)
    reminder = condenser.get_reminder(reset_view, llm, token_count=80_000)
    assert isinstance(reminder, ContextWindowReminderEvent)
    reset_view.append_event(reminder)
    reset_view.append_event(Condensation(llm_response_id="ack"))
    assert condenser.get_reminder(reset_view, llm, token_count=80_000) is None
    reset_view.append_event(CondensationRequest(trigger_action_id="reset-action"))

    result = condenser.condense(reset_view)
    assert isinstance(result, Condensation)
    reset_view.append_event(result)

    assert reset_view.pending_condensation_request is None
    assert not reset_view.unhandled_condensation_request
    assert condenser.get_reminder(reset_view, llm, token_count=80_000) is not None
    assert condenser.get_reminder(reset_view, llm, token_count=79_999) is None
    assert condenser.get_reminder(reset_view, llm, token_count=100_000) is None
    assert condenser.is_over_capacity(reset_view, llm, token_count=100_000)


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.asyncio
async def test_explicit_summary_preserves_users_and_system(
    reset_view: View, use_async: bool
):
    llm = TestLLM.from_messages(
        [
            RuntimeError("retry summary"),
            Message(
                role="assistant",
                content=[TextContent(text="Earlier implementation work.")],
            ),
        ]
    )
    reset_view.append_event(CondensationRequest())
    condenser = AgentResetCondenser()

    result = (
        await condenser.acondense(reset_view, llm)
        if use_async
        else condenser.condense(reset_view, llm)
    )

    assert isinstance(result, Condensation)
    assert llm.call_count == 2
    assert result.llm_response_id == "test-response-2"
    retained = result.apply(reset_view.events)
    assert [event.id for event in retained[:2]] == ["system", "old-user"]
    assert retained[2].id == result.summary_event.id
    assert {
        event.id
        for event in retained
        if isinstance(event, MessageEvent) and event.source == "user"
    } == {"old-user", "concurrent-user-1", "concurrent-user-2"}


def test_summary_refuses_to_discard_protected_input():
    view = View.from_events(
        [
            MessageEvent(
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="User input")]
                ),
            )
        ]
    )
    llm = TestLLM.from_messages([])

    with pytest.raises(NoCondensationAvailableException, match="No history"):
        AgentResetCondenser().hard_context_reset(view, llm)
    assert llm.call_count == 0


def test_summary_requires_actual_token_reduction():
    view = View.from_events(
        [
            MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text="Short")]
                ),
            )
        ]
    )
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Longer summary " * 100)])]
    )

    with pytest.raises(NoCondensationAvailableException, match="did not reduce"):
        AgentResetCondenser().hard_context_reset(view, llm)


def test_reset_requires_successful_observation(reset_view: View):
    reset_view.events = [
        event for event in reset_view.events if event.id != "reset-result"
    ]
    with pytest.raises(NoCondensationAvailableException, match="completed, successful"):
        AgentResetCondenser().condense(reset_view)


def test_ordinary_view_is_unchanged():
    view = View.from_events(
        [
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="Work")]),
            )
        ]
    )
    assert AgentResetCondenser().condense(view) is view
