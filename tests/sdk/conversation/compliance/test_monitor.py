"""Tests for the APIComplianceMonitor."""

from openhands.sdk.conversation.compliance import (
    APIComplianceMonitor,
    InterleavedMessageProperty,
)
from openhands.sdk.conversation.compliance.base import (
    APICompliancePropertyBase,
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.event import LLMConvertibleEvent
from tests.sdk.conversation.compliance.conftest import (
    make_action_event,
    make_observation_event,
    make_orphan_observation_event,
    make_user_message_event,
)


def test_monitor_no_violations_normal_flow():
    """Normal conversation flow should have no violations."""
    monitor = APIComplianceMonitor()
    all_violations: list = []

    # Normal flow: action -> observation -> message
    action = make_action_event(tool_call_id="call_1")
    violations = monitor.process_event(action)
    all_violations.extend(violations)
    assert len(violations) == 0

    obs = make_observation_event(action)
    violations = monitor.process_event(obs)
    all_violations.extend(violations)
    assert len(violations) == 0

    user_msg = make_user_message_event()
    violations = monitor.process_event(user_msg)
    all_violations.extend(violations)
    assert len(violations) == 0

    assert len(all_violations) == 0


def test_monitor_detects_interleaved_message():
    """Monitor should detect interleaved message violation."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_1")
    monitor.process_event(action)

    # User message before observation - violation
    user_msg = make_user_message_event()
    violations = monitor.process_event(user_msg)

    assert len(violations) == 1
    assert violations[0].property_name == "interleaved_message"


def test_monitor_detects_multiple_violations():
    """Monitor should detect multiple violations."""
    monitor = APIComplianceMonitor()

    # Orphan observation (unmatched + order violation)
    orphan = make_orphan_observation_event(tool_call_id="call_unknown")
    violations = monitor.process_event(orphan)

    # Should detect both tool_result_order and unmatched_tool_result
    assert len(violations) >= 2
    property_names = {v.property_name for v in violations}
    assert "tool_result_order" in property_names
    assert "unmatched_tool_result" in property_names


def test_monitor_returns_violations_per_call():
    """Monitor returns violations for each call, caller can accumulate."""
    monitor = APIComplianceMonitor()
    all_violations: list = []

    # First violation
    action = make_action_event(tool_call_id="call_1")
    all_violations.extend(monitor.process_event(action))
    violations = monitor.process_event(make_user_message_event())  # interleaved
    all_violations.extend(violations)

    initial_count = len(all_violations)
    assert initial_count > 0

    # Second violation
    violations = monitor.process_event(
        make_orphan_observation_event(tool_call_id="unknown")
    )
    all_violations.extend(violations)

    assert len(all_violations) > initial_count


def test_monitor_custom_properties():
    """Monitor can be initialized with custom properties."""
    # Only check interleaved messages
    monitor = APIComplianceMonitor(properties=[InterleavedMessageProperty()])

    # This would normally trigger tool_result_order violation
    orphan = make_orphan_observation_event(tool_call_id="call_unknown")
    violations = monitor.process_event(orphan)

    # But we only have InterleavedMessageProperty, so no violations
    assert len(violations) == 0


def test_monitor_state_persists_across_events():
    """Monitor state should persist correctly across events."""
    monitor = APIComplianceMonitor()

    # Add action
    action1 = make_action_event(tool_call_id="call_1")
    monitor.process_event(action1)

    assert "call_1" in monitor.state.pending_tool_call_ids
    assert "call_1" in monitor.state.all_tool_call_ids

    # Add observation
    obs1 = make_observation_event(action1)
    monitor.process_event(obs1)

    assert "call_1" not in monitor.state.pending_tool_call_ids
    assert "call_1" in monitor.state.completed_tool_call_ids
    assert "call_1" in monitor.state.all_tool_call_ids


def test_monitor_parallel_tool_calls():
    """Monitor should handle parallel tool calls correctly."""
    monitor = APIComplianceMonitor()

    # Three parallel actions
    action1 = make_action_event(tool_call_id="call_sf")
    action2 = make_action_event(tool_call_id="call_tokyo")
    action3 = make_action_event(tool_call_id="call_paris")

    for action in [action1, action2, action3]:
        monitor.process_event(action)

    assert len(monitor.state.pending_tool_call_ids) == 3

    # Two results arrive
    monitor.process_event(make_observation_event(action1))
    monitor.process_event(make_observation_event(action2))

    assert len(monitor.state.pending_tool_call_ids) == 1
    assert "call_paris" in monitor.state.pending_tool_call_ids

    # User message with one pending - violation
    violations = monitor.process_event(make_user_message_event())
    assert len(violations) == 1
    assert "call_paris" in str(violations[0].context)


def test_monitor_handles_buggy_property_gracefully():
    """Monitor should handle exceptions in property checks gracefully.

    A buggy property that raises an exception should not crash the monitor.
    Instead, the exception is logged and processing continues. This ensures
    observation mode is robust against buggy properties.
    """

    class BuggyProperty(APICompliancePropertyBase):
        @property
        def name(self) -> str:
            return "buggy"

        def check(
            self,
            event: LLMConvertibleEvent,
            state: ComplianceState,
        ) -> ComplianceViolation | None:
            raise ValueError("Oops!")

    monitor = APIComplianceMonitor(properties=[BuggyProperty()])

    # Should not raise - the exception should be caught and logged
    # We expect an empty list because the buggy property doesn't return a violation
    # (it raises instead)
    violations = monitor.process_event(make_user_message_event())

    # The monitor should continue working despite the error
    assert violations == []
