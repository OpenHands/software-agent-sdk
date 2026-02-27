"""Tests for API compliance monitoring.

These tests verify the monitor detects all 8 API compliance patterns from
tests/integration/tests/a*.py:

- a01: Unmatched tool_use (message while calls pending)
- a02: Unmatched tool_result (result with unknown ID)
- a03: Interleaved user message (user message while calls pending)
- a04: Interleaved assistant message (assistant message while calls pending)
- a05: Duplicate tool_call_id (result for already-completed ID)
- a06: Wrong tool_call_id (result with wrong/unknown ID)
- a07: Parallel missing result (message before all parallel results)
- a08: Parallel wrong order (result before action)
"""

from openhands.sdk.conversation.compliance import APIComplianceMonitor
from tests.sdk.conversation.compliance.conftest import (
    make_action_event,
    make_assistant_message_event,
    make_observation_event,
    make_orphan_observation_event,
    make_user_message_event,
)


# =============================================================================
# Interleaved Message Violations (a01, a03, a04, a07)
# =============================================================================


def test_no_violation_message_when_no_pending_actions():
    """User message is fine when no tool calls are pending."""
    monitor = APIComplianceMonitor()

    user_msg = make_user_message_event()
    violations = monitor.process_event(user_msg)

    assert len(violations) == 0


def test_violation_user_message_with_pending_action():
    """User message while action is pending violates the property (a01/a03)."""
    monitor = APIComplianceMonitor()

    # Add an action (now pending)
    action = make_action_event(tool_call_id="call_123")
    monitor.process_event(action)

    # User message before observation - violation
    user_msg = make_user_message_event()
    violations = monitor.process_event(user_msg)

    assert len(violations) == 1
    assert violations[0].property_name == "interleaved_message"
    assert "pending" in violations[0].description.lower()
    assert "call_123" in str(violations[0].context)


def test_violation_assistant_message_with_pending_action():
    """Assistant message while action is pending violates the property (a04)."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_456")
    monitor.process_event(action)

    assistant_msg = make_assistant_message_event()
    violations = monitor.process_event(assistant_msg)

    assert len(violations) == 1
    assert violations[0].property_name == "interleaved_message"


def test_violation_parallel_missing_result():
    """User message with partial parallel results violates property (a07)."""
    monitor = APIComplianceMonitor()

    # Three parallel actions
    action1 = make_action_event(tool_call_id="call_sf")
    action2 = make_action_event(tool_call_id="call_tokyo")
    action3 = make_action_event(tool_call_id="call_paris")
    for action in [action1, action2, action3]:
        monitor.process_event(action)

    # Two results arrive
    monitor.process_event(make_observation_event(action1))
    monitor.process_event(make_observation_event(action2))
    # call_paris is still pending

    user_msg = make_user_message_event("What about Paris?")
    violations = monitor.process_event(user_msg)

    assert len(violations) == 1
    assert "call_paris" in str(violations[0].context)


def test_no_violation_for_action_events():
    """ActionEvent itself doesn't trigger violation (always allowed)."""
    monitor = APIComplianceMonitor()

    # Even with pending actions, a new action is fine
    action1 = make_action_event(tool_call_id="call_existing")
    monitor.process_event(action1)

    action2 = make_action_event(tool_call_id="call_new")
    violations = monitor.process_event(action2)

    assert len(violations) == 0


def test_no_violation_for_matching_observation():
    """ObservationEvent matching a pending action is allowed."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_789")
    monitor.process_event(action)

    obs = make_observation_event(action)
    violations = monitor.process_event(obs)

    assert len(violations) == 0


# =============================================================================
# Unmatched Tool Result Violations (a02, a06, a08)
# =============================================================================


def test_no_violation_when_action_exists():
    """Tool result is fine when its tool_call_id is pending."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_valid")
    monitor.process_event(action)

    obs = make_observation_event(action)
    violations = monitor.process_event(obs)

    assert len(violations) == 0


def test_violation_unknown_tool_call_id():
    """Tool result with unknown tool_call_id violates property (a02/a06/a08)."""
    monitor = APIComplianceMonitor()

    # No actions have been seen
    orphan_obs = make_orphan_observation_event(tool_call_id="call_unknown")
    violations = monitor.process_event(orphan_obs)

    assert len(violations) == 1
    assert violations[0].property_name == "unmatched_tool_result"
    assert "call_unknown" in violations[0].description


def test_violation_wrong_tool_call_id():
    """Tool result referencing wrong tool_call_id violates property (a06)."""
    monitor = APIComplianceMonitor()

    # We have action with call_correct, but result references call_wrong
    action = make_action_event(tool_call_id="call_correct")
    monitor.process_event(action)

    orphan_obs = make_orphan_observation_event(tool_call_id="call_wrong")
    violations = monitor.process_event(orphan_obs)

    assert len(violations) == 1
    assert violations[0].property_name == "unmatched_tool_result"
    assert "call_wrong" in violations[0].description


# =============================================================================
# Duplicate Tool Result Violations (a05)
# =============================================================================


def test_no_violation_first_result():
    """First tool result for a tool_call_id is fine."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_first")
    monitor.process_event(action)

    obs = make_observation_event(action)
    violations = monitor.process_event(obs)

    assert len(violations) == 0


def test_violation_duplicate_result():
    """Second tool result for same tool_call_id violates property (a05)."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_duplicate")
    monitor.process_event(action)

    # First result - fine
    obs1 = make_observation_event(action)
    violations = monitor.process_event(obs1)
    assert len(violations) == 0

    # Second result for same ID - violation
    obs2 = make_orphan_observation_event(tool_call_id="call_duplicate")
    violations = monitor.process_event(obs2)

    assert len(violations) == 1
    assert violations[0].property_name == "duplicate_tool_result"
    assert "call_duplicate" in violations[0].description


# =============================================================================
# State Update Tests
# =============================================================================


def test_state_update_action_adds_pending():
    """Adding an action should update pending_tool_call_ids."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_new")
    monitor.process_event(action)

    assert "call_new" in monitor.state.pending_tool_call_ids


def test_state_update_observation_resolves_pending():
    """Adding an observation should move from pending to completed."""
    monitor = APIComplianceMonitor()

    action = make_action_event(tool_call_id="call_resolve")
    monitor.process_event(action)
    assert "call_resolve" in monitor.state.pending_tool_call_ids

    obs = make_observation_event(action)
    monitor.process_event(obs)

    assert "call_resolve" not in monitor.state.pending_tool_call_ids
    assert "call_resolve" in monitor.state.completed_tool_call_ids
