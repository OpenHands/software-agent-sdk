"""Property tests: a View built from any event history is safe to send to an LLM.

A history is a sequence of steps: a user message, an error result for a tool
call that was never made, a parallel tool-call batch whose calls each receive
zero, one or two results, or a condensation forgetting earlier events. For
every such history the view pairs each tool call with exactly one result, keeps
or drops a batch as a whole, has well-defined manipulation indices (also when
built incrementally), and is a fixpoint of property enforcement.
"""

from collections.abc import Sequence

from hypothesis import given, strategies as st

from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    ObservationBaseEvent,
)
from tests.sdk.context.view.conftest import (
    create_action_event,
    create_observation_event,
    message_event,
)


TOOL_RESULTS = st.sampled_from(["observation", "error", "both", "none"])

# Steps: "user_message", "orphan_error", a list with one TOOL_RESULTS entry per
# parallel tool call, or a set of history positions a condensation forgets.
STEPS = st.lists(
    st.one_of(
        st.sampled_from(["user_message", "orphan_error"]),
        st.lists(TOOL_RESULTS, min_size=1, max_size=3),
        st.sets(st.integers(min_value=0, max_value=40), max_size=3),
    ),
    max_size=10,
)


def _error_result(tool_call_id: str) -> AgentErrorEvent:
    return AgentErrorEvent(
        error="interrupted", tool_name="test_tool", tool_call_id=tool_call_id
    )


def _build_history(steps: Sequence[str | list[str] | set[int]]) -> list[Event]:
    events: list[Event] = []
    for n, step in enumerate(steps):
        match step:
            case "user_message":
                events.append(message_event("hi"))
            case "orphan_error":
                events.append(_error_result(f"orphan_{n}"))
            case set():
                forgotten = {events[i].id for i in step if i < len(events)}
                events.append(
                    Condensation(
                        forgotten_event_ids=forgotten,
                        llm_response_id=f"condensation_{n}",
                    )
                )
            case list():
                actions = [
                    create_action_event(f"response_{n}", f"call_{n}_{k}")
                    for k in range(len(step))
                ]
                events.extend(actions)
                for action, results in zip(actions, step):
                    if results in ("observation", "both"):
                        events.append(create_observation_event(action.tool_call_id))
                    if results in ("error", "both"):
                        events.append(_error_result(action.tool_call_id))
            case _:
                raise AssertionError(f"unknown step {step!r}")
    return events


@given(STEPS)
def test_manipulation_indices_defined_on_built_view(
    steps: list[str | list[str] | set[int]],
) -> None:
    view = View.from_events(_build_history(steps))
    assert view.manipulation_indices <= set(range(len(view) + 1))


@given(STEPS)
def test_manipulation_indices_defined_on_incrementally_built_view(
    steps: list[str | list[str] | set[int]],
) -> None:
    view = View()
    for event in _build_history(steps):
        view.append_event(event)
    assert view.manipulation_indices <= set(range(len(view) + 1))


@given(STEPS)
def test_each_tool_call_paired_with_exactly_one_result(
    steps: list[str | list[str] | set[int]],
) -> None:
    view = View.from_events(_build_history(steps))
    call_ids = [e.tool_call_id for e in view.events if isinstance(e, ActionEvent)]
    result_ids = [
        e.tool_call_id for e in view.events if isinstance(e, ObservationBaseEvent)
    ]
    assert sorted(call_ids) == sorted(result_ids)


@given(STEPS)
def test_tool_call_batch_kept_or_dropped_as_a_whole(
    steps: list[str | list[str] | set[int]],
) -> None:
    history = _build_history(steps)
    view = View.from_events(history)
    kept = {e.id: e.llm_response_id for e in view.events if isinstance(e, ActionEvent)}
    for event in history:
        if isinstance(event, ActionEvent) and event.llm_response_id in kept.values():
            assert event.id in kept


@given(STEPS)
def test_enforcement_is_a_fixpoint(steps: list[str | list[str] | set[int]]) -> None:
    view = View.from_events(_build_history(steps))
    rebuilt = View.from_events(view.events)
    assert [e.id for e in rebuilt.events] == [e.id for e in view.events]
