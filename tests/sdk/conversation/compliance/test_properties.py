"""Tests for individual API compliance properties.

These tests correspond to the API compliance patterns in tests/integration/tests/a*.py:
- a01: Unmatched tool_use -> InterleavedMessageProperty
- a02: Unmatched tool_result -> UnmatchedToolResultProperty
- a03: Interleaved user message -> InterleavedMessageProperty
- a04: Interleaved assistant message -> InterleavedMessageProperty
- a05: Duplicate tool_call_id -> DuplicateToolResultProperty
- a06: Wrong tool_call_id -> UnmatchedToolResultProperty
- a07: Parallel missing result -> InterleavedMessageProperty
- a08: Parallel wrong order -> ToolResultOrderProperty
"""

from openhands.sdk.conversation.compliance import (
    ComplianceState,
    DuplicateToolResultProperty,
    InterleavedMessageProperty,
    ToolResultOrderProperty,
    UnmatchedToolResultProperty,
)
from tests.sdk.conversation.compliance.conftest import (
    make_action_event,
    make_assistant_message_event,
    make_observation_event,
    make_orphan_observation_event,
    make_user_message_event,
)


# =============================================================================
# InterleavedMessageProperty Tests
# Detects: a01, a03, a04, a07
# =============================================================================


def test_interleaved_no_violation_when_no_pending_actions():
    """User message is fine when no tool calls are pending."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    user_msg = make_user_message_event()
    violation = prop.check(user_msg, state)

    assert violation is None


def test_interleaved_violation_user_message_with_pending_action():
    """User message while action is pending violates the property (a01/a03)."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    # Simulate an action that's pending
    action = make_action_event(tool_call_id="call_123")
    state.pending_tool_call_ids["call_123"] = action.id

    user_msg = make_user_message_event()
    violation = prop.check(user_msg, state)

    assert violation is not None
    assert violation.property_name == "interleaved_message"
    assert "pending" in violation.description.lower()
    assert "call_123" in str(violation.context)


def test_interleaved_violation_assistant_message_with_pending_action():
    """Assistant message while action is pending violates the property (a04)."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    action = make_action_event(tool_call_id="call_456")
    state.pending_tool_call_ids["call_456"] = action.id

    assistant_msg = make_assistant_message_event()
    violation = prop.check(assistant_msg, state)

    assert violation is not None
    assert violation.property_name == "interleaved_message"


def test_interleaved_violation_parallel_missing_result():
    """User message with partial parallel results violates property (a07)."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    # Simulate 3 parallel actions, only 2 have results
    state.pending_tool_call_ids["call_sf"] = "action_sf"
    state.pending_tool_call_ids["call_tokyo"] = "action_tokyo"
    state.pending_tool_call_ids["call_paris"] = "action_paris"

    # Two results arrive
    state.pending_tool_call_ids.pop("call_sf")
    state.completed_tool_call_ids.add("call_sf")
    state.pending_tool_call_ids.pop("call_tokyo")
    state.completed_tool_call_ids.add("call_tokyo")
    # call_paris is still pending

    user_msg = make_user_message_event("What about Paris?")
    violation = prop.check(user_msg, state)

    assert violation is not None
    assert "call_paris" in str(violation.context)


def test_interleaved_no_violation_for_action_events():
    """ActionEvent itself doesn't trigger interleaved violation."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    # Even with pending actions, a new action is fine
    state.pending_tool_call_ids["call_existing"] = "action_existing"
    action = make_action_event()

    violation = prop.check(action, state)
    assert violation is None


def test_interleaved_no_violation_for_observation_events():
    """ObservationEvent doesn't trigger interleaved violation."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    action = make_action_event(tool_call_id="call_789")
    state.pending_tool_call_ids["call_789"] = action.id

    obs = make_observation_event(action)
    violation = prop.check(obs, state)

    assert violation is None


# =============================================================================
# UnmatchedToolResultProperty Tests
# Detects: a02, a06
# =============================================================================


def test_unmatched_result_no_violation_when_action_exists():
    """Tool result is fine when its tool_call_id exists."""
    prop = UnmatchedToolResultProperty()
    state = ComplianceState()

    action = make_action_event(tool_call_id="call_valid")
    state.all_tool_call_ids.add("call_valid")
    state.pending_tool_call_ids["call_valid"] = action.id

    obs = make_observation_event(action)
    violation = prop.check(obs, state)

    assert violation is None


def test_unmatched_result_violation_unknown_tool_call_id():
    """Tool result with unknown tool_call_id violates property (a02/a06)."""
    prop = UnmatchedToolResultProperty()
    state = ComplianceState()

    # No actions have been seen
    orphan_obs = make_orphan_observation_event(tool_call_id="call_unknown")
    violation = prop.check(orphan_obs, state)

    assert violation is not None
    assert violation.property_name == "unmatched_tool_result"
    assert "call_unknown" in violation.description


def test_unmatched_result_violation_wrong_tool_call_id():
    """Tool result referencing wrong tool_call_id violates property (a06)."""
    prop = UnmatchedToolResultProperty()
    state = ComplianceState()

    # We have action with call_correct, but result references call_wrong
    state.all_tool_call_ids.add("call_correct")
    state.pending_tool_call_ids["call_correct"] = "action_correct"

    orphan_obs = make_orphan_observation_event(tool_call_id="call_wrong")
    violation = prop.check(orphan_obs, state)

    assert violation is not None
    assert "call_wrong" in violation.description


def test_unmatched_result_no_violation_for_non_observation():
    """Non-observation events don't trigger this property."""
    prop = UnmatchedToolResultProperty()
    state = ComplianceState()

    user_msg = make_user_message_event()
    violation = prop.check(user_msg, state)

    assert violation is None


# =============================================================================
# DuplicateToolResultProperty Tests
# Detects: a05
# =============================================================================


def test_duplicate_result_no_violation_first_result():
    """First tool result for a tool_call_id is fine."""
    prop = DuplicateToolResultProperty()
    state = ComplianceState()

    action = make_action_event(tool_call_id="call_first")
    state.all_tool_call_ids.add("call_first")
    state.pending_tool_call_ids["call_first"] = action.id

    obs = make_observation_event(action)
    violation = prop.check(obs, state)

    assert violation is None


def test_duplicate_result_violation_second_result():
    """Second tool result for same tool_call_id violates property (a05)."""
    prop = DuplicateToolResultProperty()
    state = ComplianceState()

    # First result already processed
    state.completed_tool_call_ids.add("call_duplicate")
    state.all_tool_call_ids.add("call_duplicate")

    # Second result arrives
    duplicate_obs = make_orphan_observation_event(tool_call_id="call_duplicate")
    violation = prop.check(duplicate_obs, state)

    assert violation is not None
    assert violation.property_name == "duplicate_tool_result"
    assert "call_duplicate" in violation.description


def test_duplicate_result_no_violation_for_non_observation():
    """Non-observation events don't trigger this property."""
    prop = DuplicateToolResultProperty()
    state = ComplianceState()

    action = make_action_event()
    violation = prop.check(action, state)

    assert violation is None


# =============================================================================
# ToolResultOrderProperty Tests
# Detects: a08
# =============================================================================


def test_result_order_no_violation_normal_order():
    """Tool result after action is fine."""
    prop = ToolResultOrderProperty()
    state = ComplianceState()

    action = make_action_event(tool_call_id="call_ordered")
    state.all_tool_call_ids.add("call_ordered")
    state.pending_tool_call_ids["call_ordered"] = action.id

    obs = make_observation_event(action)
    violation = prop.check(obs, state)

    assert violation is None


def test_result_order_violation_result_before_action():
    """Tool result before action violates property (a08)."""
    prop = ToolResultOrderProperty()
    state = ComplianceState()

    # Result arrives, but no action has been seen with this ID
    # (different from unmatched - here the action may come later)
    orphan_obs = make_orphan_observation_event(tool_call_id="call_early")
    violation = prop.check(orphan_obs, state)

    assert violation is not None
    assert violation.property_name == "tool_result_order"
    assert "call_early" in violation.description


def test_result_order_no_violation_for_non_observation():
    """Non-observation events don't trigger this property."""
    prop = ToolResultOrderProperty()
    state = ComplianceState()

    user_msg = make_user_message_event()
    violation = prop.check(user_msg, state)

    assert violation is None


# =============================================================================
# State Update Tests
# =============================================================================


def test_state_update_action_adds_pending():
    """Adding an action should update pending_tool_call_ids."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    action = make_action_event(tool_call_id="call_new")
    prop.update_state(action, state)

    assert "call_new" in state.pending_tool_call_ids
    assert "call_new" in state.all_tool_call_ids


def test_state_update_observation_resolves_pending():
    """Adding an observation should move from pending to completed."""
    prop = InterleavedMessageProperty()
    state = ComplianceState()

    # Setup: action is pending
    action = make_action_event(tool_call_id="call_resolve")
    state.pending_tool_call_ids["call_resolve"] = action.id
    state.all_tool_call_ids.add("call_resolve")

    obs = make_observation_event(action)
    prop.update_state(obs, state)

    assert "call_resolve" not in state.pending_tool_call_ids
    assert "call_resolve" in state.completed_tool_call_ids
