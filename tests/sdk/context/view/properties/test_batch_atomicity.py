"""Tests for BatchAtomicityProperty.

This module tests that the BatchAtomicityProperty correctly ensures all events
from the same batch (sharing the same llm_response_id) form an atomic unit.
"""

from collections.abc import Sequence

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.batch_atomicity import BatchAtomicityProperty
from openhands.sdk.event import LLMConvertibleEvent
from tests.sdk.context.view.properties.conftest import create_action_event


class TestBatchAtomicityPropertyBase:
    """Base class for BatchAtomicityProperty test suites."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = BatchAtomicityProperty()


class TestBatchAtomicityPropertyEnforcement(TestBatchAtomicityPropertyBase):
    """Tests for BatchAtomicityProperty enforcement."""

    def test_partial_batch_forgotten(self) -> None:
        """Test that if one event in a batch is forgotten, all events in that batch
        are forgotten.

        This simulates the scenario where condensation forgets some but not all
        actions from a batch. The batch atomicity logic should ensure that all
        actions in the batch are removed.
        """
        # Create a batch of 4 actions from the same LLM response
        llm_response_id = "response_1"

        action1 = create_action_event("action_1", llm_response_id, "tool_call_1")
        action2 = create_action_event("action_2", llm_response_id, "tool_call_2")
        action3 = create_action_event("action_3", llm_response_id, "tool_call_3")
        action4 = create_action_event("action_4", llm_response_id, "tool_call_4")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action1, action2, action3, action4]

        # Current view has action1, action2, action3 forgotten but action4 kept
        # This simulates what might happen if the condenser uses event indices
        # without considering batch boundaries
        current_view_events: list[LLMConvertibleEvent] = [action4]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # action4 should be forgotten due to batch atomicity
        assert action4.id in events_to_remove

    def test_complete_batch_forgotten(self) -> None:
        """Test that when all events in a batch are forgotten, they're all removed."""
        llm_response_id = "response_1"

        action1 = create_action_event("action_1", llm_response_id, "tool_call_1")
        action2 = create_action_event("action_2", llm_response_id, "tool_call_2")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action1, action2]

        # Current view has no actions (all forgotten)
        current_view_events: Sequence[LLMConvertibleEvent] = []

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing more to remove since the batch is already gone
        assert len(events_to_remove) == 0

    def test_no_forgetting_preserves_batch(self) -> None:
        """Test that when no events in a batch are forgotten, all are preserved."""
        llm_response_id = "response_1"

        action1 = create_action_event("action_1", llm_response_id, "tool_call_1")
        action2 = create_action_event("action_2", llm_response_id, "tool_call_2")
        action3 = create_action_event("action_3", llm_response_id, "tool_call_3")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action1, action2, action3]

        # Current view has all actions
        current_view_events: list[LLMConvertibleEvent] = [action1, action2, action3]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing should be removed
        assert len(events_to_remove) == 0

    def test_multiple_batches(self) -> None:
        """Test that batch atomicity works correctly with multiple separate batches.

        When only one action from a batch is forgotten, all actions in that batch
        should be forgotten. But different batches should be independent.
        """
        # First batch
        batch1_id = "response_1"
        action1_1 = create_action_event("action_1_1", batch1_id, "tool_call_1")
        action1_2 = create_action_event("action_1_2", batch1_id, "tool_call_2")

        # Second batch
        batch2_id = "response_2"
        action2_1 = create_action_event("action_2_1", batch2_id, "tool_call_3")
        action2_2 = create_action_event("action_2_2", batch2_id, "tool_call_4")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [
            action1_1,
            action1_2,
            action2_1,
            action2_2,
        ]

        # Current view has action1_2 forgotten but action1_1 kept (partial batch1)
        # and action2_1, action2_2 kept (complete batch2)
        current_view_events: list[LLMConvertibleEvent] = [
            action1_1,
            action2_1,
            action2_2,
        ]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # First batch should be removed since we're missing the second action
        assert action1_1.id in events_to_remove

        # Second batch should be preserved entirely
        assert action2_1.id not in events_to_remove
        assert action2_2.id not in events_to_remove

    def test_first_action_of_batch_forgotten(self) -> None:
        """Test that forgetting only the first action of a batch causes entire batch
        to be forgotten.
        """
        llm_response_id = "response_1"

        action1 = create_action_event("action_1", llm_response_id, "tool_call_1")
        action2 = create_action_event("action_2", llm_response_id, "tool_call_2")
        action3 = create_action_event("action_3", llm_response_id, "tool_call_3")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action1, action2, action3]

        # Current view has action2 and action3 (action1 forgotten)
        current_view_events: list[LLMConvertibleEvent] = [action2, action3]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Both action2 and action3 should be forgotten
        assert action2.id in events_to_remove
        assert action3.id in events_to_remove

    def test_middle_action_of_batch_forgotten(self) -> None:
        """Test that forgetting a middle action causes entire batch to be forgotten."""
        llm_response_id = "response_1"

        action1 = create_action_event("action_1", llm_response_id, "tool_call_1")
        action2 = create_action_event("action_2", llm_response_id, "tool_call_2")
        action3 = create_action_event("action_3", llm_response_id, "tool_call_3")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action1, action2, action3]

        # Current view has action1 and action3 (action2 forgotten)
        current_view_events: list[LLMConvertibleEvent] = [action1, action3]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Both action1 and action3 should be forgotten
        assert action1.id in events_to_remove
        assert action3.id in events_to_remove

    def test_different_batches_independent(self) -> None:
        """Test that batch atomicity only affects events in the same batch."""
        batch1_id = "response_1"
        batch2_id = "response_2"

        # First batch
        action1_1 = create_action_event("action_1_1", batch1_id, "tool_call_1")
        action1_2 = create_action_event("action_1_2", batch1_id, "tool_call_2")

        # Second batch
        action2_1 = create_action_event("action_2_1", batch2_id, "tool_call_3")
        action2_2 = create_action_event("action_2_2", batch2_id, "tool_call_4")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [
            action1_1,
            action1_2,
            action2_1,
            action2_2,
        ]

        # Current view has all events from both batches
        current_view_events: list[LLMConvertibleEvent] = [
            action1_1,
            action1_2,
            action2_1,
            action2_2,
        ]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing should be removed
        assert len(events_to_remove) == 0

    def test_single_action_batch(self) -> None:
        """Test that batches with a single action work correctly."""
        llm_response_id = "response_1"

        action = create_action_event("action_1", llm_response_id, "tool_call_1")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action]

        # Current view has the action
        current_view_events: list[LLMConvertibleEvent] = [action]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing should be removed
        assert len(events_to_remove) == 0

    def test_single_action_forgotten(self) -> None:
        """Test that a forgotten single-action batch is handled correctly."""
        llm_response_id = "response_1"

        action = create_action_event("action_1", llm_response_id, "tool_call_1")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action]

        # Current view has no actions (forgotten)
        current_view_events: Sequence[LLMConvertibleEvent] = []

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing more to remove
        assert len(events_to_remove) == 0

    def test_partial_batch_across_batches(self) -> None:
        """Test that partial batches across different LLM responses are handled
        independently.
        """
        # First batch - partial
        batch1_id = "response_1"
        action1_1 = create_action_event("action_1_1", batch1_id, "tool_call_1")
        action1_2 = create_action_event("action_1_2", batch1_id, "tool_call_2")

        # Second batch - complete
        batch2_id = "response_2"
        action2_1 = create_action_event("action_2_1", batch2_id, "tool_call_3")

        # All events in the conversation
        all_events: Sequence[LLMConvertibleEvent] = [action1_1, action1_2, action2_1]

        # Current view has action1_2 and action2_1 (action1_1 forgotten)
        current_view_events: list[LLMConvertibleEvent] = [action1_2, action2_1]

        # Enforce batch atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # action1_2 should be removed due to batch atomicity
        assert action1_2.id in events_to_remove

        # action2_1 should NOT be removed (its batch is complete)
        assert action2_1.id not in events_to_remove


class TestBatchAtomicityPropertyManipulationIndices(TestBatchAtomicityPropertyBase):
    """Tests for BatchAtomicityProperty manipulation indices."""

    def test_same_batch_no_manipulation_index(self) -> None:
        """Test that events in the same batch cannot be split by manipulation."""
        llm_response_id = "response_1"

        action1 = create_action_event("action_1", llm_response_id, "tool_call_1")
        action2 = create_action_event("action_2", llm_response_id, "tool_call_2")
        action3 = create_action_event("action_3", llm_response_id, "tool_call_3")

        current_view_events: list[LLMConvertibleEvent] = [action1, action2, action3]

        indices = self.property.manipulation_indices(current_view_events)

        # Index 1 (between action1 and action2) should not be manipulatable
        assert 1 not in indices
        # Index 2 (between action2 and action3) should not be manipulatable
        assert 2 not in indices

    def test_different_batches_allow_manipulation(self) -> None:
        """Test that events in different batches can be split by manipulation."""
        batch1_id = "response_1"
        batch2_id = "response_2"

        action1 = create_action_event("action_1", batch1_id, "tool_call_1")
        action2 = create_action_event("action_2", batch2_id, "tool_call_2")

        current_view_events: list[LLMConvertibleEvent] = [action1, action2]

        indices = self.property.manipulation_indices(current_view_events)

        # Index 1 (between action1 and action2) should be manipulatable
        # since they're in different batches
        assert 1 in indices

    def test_single_event_complete_indices(self) -> None:
        """Test that a single event has complete manipulation indices."""
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_1", "response_1", "tool_call_1")
        ]

        indices = self.property.manipulation_indices(current_view_events)
        assert indices == ManipulationIndices.complete(current_view_events)

    def test_empty_events_complete_indices(self) -> None:
        """Test that an empty event list has complete manipulation indices."""
        current_view_events: list[LLMConvertibleEvent] = []

        indices = self.property.manipulation_indices(current_view_events)
        assert indices == ManipulationIndices.complete(current_view_events)
