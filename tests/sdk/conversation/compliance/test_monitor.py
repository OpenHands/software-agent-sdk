"""Tests for the APIComplianceMonitor."""

from unittest.mock import patch

from openhands.sdk.conversation.compliance import APIComplianceMonitor
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


def test_monitor_detects_orphan_observation():
    """Monitor should detect orphan observation as single violation."""
    monitor = APIComplianceMonitor()

    # Orphan observation (unknown tool_call_id)
    orphan = make_orphan_observation_event(tool_call_id="call_unknown")
    violations = monitor.process_event(orphan)

    # Should detect exactly one violation (unmatched_tool_result)
    assert len(violations) == 1
    assert violations[0].property_name == "unmatched_tool_result"


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


def test_monitor_state_persists_across_events():
    """Monitor state should persist correctly across events."""
    monitor = APIComplianceMonitor()

    # Add action
    action1 = make_action_event(tool_call_id="call_1")
    monitor.process_event(action1)

    assert "call_1" in monitor.state.pending_tool_call_ids

    # Add observation
    obs1 = make_observation_event(action1)
    monitor.process_event(obs1)

    assert "call_1" not in monitor.state.pending_tool_call_ids
    assert "call_1" in monitor.state.completed_tool_call_ids


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


def test_monitor_handles_check_exception_gracefully():
    """Monitor should handle exceptions in check gracefully.

    If _check_tool_call_sequence raises an exception, it should be caught
    and logged, not crash the monitor. This ensures observation mode is robust.
    """
    monitor = APIComplianceMonitor()

    with patch.object(
        monitor, "_check_tool_call_sequence", side_effect=ValueError("Oops!")
    ):
        # Should not raise - the exception should be caught and logged
        violations = monitor.process_event(make_user_message_event())

        # The monitor should continue working despite the error
        assert violations == []


def test_monitor_handles_update_exception_gracefully():
    """Monitor should handle exceptions in state update gracefully.

    If _update_state raises an exception, it should be caught and logged,
    not crash the monitor.
    """
    monitor = APIComplianceMonitor()

    with patch.object(monitor, "_update_state", side_effect=ValueError("Oops!")):
        # Should not raise - the exception should be caught and logged
        violations = monitor.process_event(make_action_event())

        # The monitor should continue working
        assert violations == []
