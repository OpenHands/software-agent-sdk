import pytest

from openhands.sdk.context.view import (
    ObservationDisposition,
    View,
    get_observation_dispositions,
)
from openhands.sdk.event import (
    AgentErrorEvent,
    Event,
    ObservationBaseEvent,
    PauseEvent,
    UserRejectObservation,
)
from openhands.sdk.event.error_classification import ErrorClassification, FailureKind
from tests.sdk.context.view.conftest import (
    create_action_event,
    create_observation_event,
    message_event,
)


@pytest.mark.parametrize("recovery_first", [True, False])
def test_disposition_agrees_with_view_without_mutating_events(recovery_first):
    action = create_action_event("response_1", "call_1")
    recovery = AgentErrorEvent(
        error="Interrupted tool execution",
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
        recovery_reason="server_restart",
        classification=ErrorClassification(kind=FailureKind.INTERNAL, retryable=False),
    )
    observation = create_observation_event(action.tool_call_id)
    first, second = (
        (recovery, observation) if recovery_first else (observation, recovery)
    )
    third = create_observation_event(action.tool_call_id, "Another late result")
    events: list[Event] = [
        message_event("Run the tool"),
        action,
        first,
        PauseEvent(),
        second,
        third,
    ]
    original = [event.model_dump_json() for event in events]
    view = View.from_events(events)
    original_view = list(view.events)

    dispositions = get_observation_dispositions(iter(events))

    assert dispositions == (
        ObservationDisposition(action.tool_call_id, first.id, (second.id, third.id)),
    )
    assert [
        event.id for event in view.events if isinstance(event, ObservationBaseEvent)
    ] == [dispositions[0].canonical_event_id]
    assert view.events == original_view
    assert [event.model_dump_json() for event in events] == original


def test_disposition_keeps_single_observations_rejections_and_internal_errors():
    actions = [create_action_event(f"response_{i}", f"call_{i}") for i in range(3)]
    observation = create_observation_event(actions[0].tool_call_id)
    rejection = UserRejectObservation(
        tool_name=actions[1].tool_name,
        tool_call_id=actions[1].tool_call_id,
        action_id=actions[1].id,
    )
    error = AgentErrorEvent(
        error="A restart occurred while this tool was in progress.",
        tool_name=actions[2].tool_name,
        tool_call_id=actions[2].tool_call_id,
        classification=ErrorClassification(kind=FailureKind.INTERNAL, retryable=False),
    )
    outcomes = [observation, rejection, error]
    events = [event for pair in zip(actions, outcomes, strict=True) for event in pair]

    dispositions = get_observation_dispositions(events)

    assert error.recovery_reason is None
    assert dispositions == tuple(
        ObservationDisposition(outcome.tool_call_id, outcome.id, ())
        for outcome in outcomes
    )
    assert [
        event.id
        for event in View.from_events(events).events
        if isinstance(event, ObservationBaseEvent)
    ] == [disposition.canonical_event_id for disposition in dispositions]
    assert get_observation_dispositions(actions) == ()
