"""Tests for ObservationUniquenessProperty.

This property guarantees at most one observation-like event per
tool_call_id, which protects ToolCallMatchingProperty's strict pairing
assumption from crash-recovery scenarios where an AgentErrorEvent and a
late ObservationEvent share the same tool_call_id.
"""

from unittest.mock import create_autospec

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.observation_uniqueness import (
    ObservationUniquenessProperty,
)
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
)


def test_enforce_drops_late_observation_after_agent_error() -> None:
    """Crash-recovery scenario: AgentErrorEvent and a late ObservationEvent
    share the same tool_call_id. The later observation-like event must be
    dropped; the first one (the AgentErrorEvent the agent already saw) is
    kept.
    """
    property = ObservationUniquenessProperty()

    action = create_autospec(ActionEvent, instance=True)
    action.tool_call_id = "call_1"
    action.id = "action_1"

    agent_error = AgentErrorEvent(
        error="A restart occurred while this tool was in progress.",
        tool_name="terminal",
        tool_call_id="call_1",
    )

    late_observation = create_autospec(ObservationEvent, instance=True)
    late_observation.tool_call_id = "call_1"
    late_observation.id = "obs_late"

    events: list[LLMConvertibleEvent] = [action, agent_error, late_observation]

    assert property.enforce(events, events) == {late_observation.id}


def test_enforce_no_duplicates_returns_empty() -> None:
    property = ObservationUniquenessProperty()

    action = create_autospec(ActionEvent, instance=True)
    action.tool_call_id = "call_1"
    action.id = "action_1"

    observation = create_autospec(ObservationEvent, instance=True)
    observation.tool_call_id = "call_1"
    observation.id = "obs_1"

    events: list[LLMConvertibleEvent] = [action, observation]
    assert property.enforce(events, events) == set()


def test_manipulation_indices_returns_complete_for_well_formed_view() -> None:
    property = ObservationUniquenessProperty()

    action = create_autospec(ActionEvent, instance=True)
    action.tool_call_id = "call_1"
    action.id = "action_1"

    observation = create_autospec(ObservationEvent, instance=True)
    observation.tool_call_id = "call_1"
    observation.id = "obs_1"

    events: list[LLMConvertibleEvent] = [action, observation]
    assert property.manipulation_indices(events) == ManipulationIndices.complete(events)


def test_manipulation_indices_warns_but_does_not_crash_on_duplicates(caplog) -> None:
    property = ObservationUniquenessProperty()

    observation_a = create_autospec(ObservationEvent, instance=True)
    observation_a.tool_call_id = "call_1"
    observation_a.id = "obs_a"

    observation_b = create_autospec(ObservationEvent, instance=True)
    observation_b.tool_call_id = "call_1"
    observation_b.id = "obs_b"

    events: list[LLMConvertibleEvent] = [observation_a, observation_b]

    with caplog.at_level("WARNING"):
        indices = property.manipulation_indices(events)

    assert indices == ManipulationIndices.complete(events)
    assert any("call_1" in rec.message for rec in caplog.records)
