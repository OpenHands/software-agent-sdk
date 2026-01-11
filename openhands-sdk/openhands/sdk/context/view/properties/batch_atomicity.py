"""Property for ensuring ActionEvent batches remain atomic."""

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.event.base import Event, LLMConvertibleEvent
from openhands.sdk.event.types import EventID


class BatchAtomicityProperty(ViewPropertyBase):
    """Ensures ActionEvents sharing the same llm_response_id form an atomic unit.

    When an LLM makes a single response containing multiple tool calls, those calls
    are semantically related. If any one is forgotten (e.g., during condensation),
    all must be forgotten together to maintain consistency.
    """

    def enforce(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> set[EventID]:
        """Enforce batch atomicity by marking all events in a partially-removed batch.

        If any ActionEvent in a batch is missing, this method will mark all other
        ActionEvents from that batch for removal.

        Args:
            current_view_events: Events currently in the view
            all_events: All events in the conversation

        Returns:
            Set of EventIDs to remove from the current view
        """
        # Build mappings from all events to understand complete batches
        all_batches = self._build_batches(all_events)
        view_batches = self._build_batches(current_view_events)

        events_to_remove: set[EventID] = set()

        # Check each batch in the original events
        for llm_response_id, action_ids in all_batches.items():
            # Get which actions from this batch are in the view
            actions_in_view = view_batches.get(llm_response_id, [])

            # If batch is partially present (some but not all actions)
            if actions_in_view and len(actions_in_view) < len(action_ids):
                # Remove all actions from this batch from the view
                events_to_remove.update(actions_in_view)

        return events_to_remove

    def manipulation_indices(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> ManipulationIndices:
        """Calculate manipulation indices that respect batch atomicity.

        Returns all indices outside of batch ranges. Within a batch (from min to max
        index), no manipulation is allowed. The range includes all actions in the batch
        and their corresponding observations.

        Args:
            current_view_events: Events currently in the view
            all_events: All events in the conversation

        Returns:
            ManipulationIndices with all valid manipulation points
        """
        from openhands.sdk.event.llm_convertible import ActionEvent, ObservationBaseEvent

        batches = self._build_batches(current_view_events)
        event_id_to_index = self._build_event_id_to_index(current_view_events)

        # Build tool_call_id to observation mapping
        tool_call_to_obs_idx: dict[str, int] = {}
        for idx, event in enumerate(current_view_events):
            if isinstance(event, ObservationBaseEvent) and event.tool_call_id:
                tool_call_to_obs_idx[event.tool_call_id] = idx

        # Find atomic ranges for each batch
        atomic_ranges: list[tuple[int, int]] = []

        for llm_response_id, action_ids in batches.items():
            # Get indices for all actions in this batch
            min_idx, max_idx = self._get_batch_extent(action_ids, event_id_to_index)

            # Extend max_idx to include all corresponding observations
            for action_id in action_ids:
                action_event = current_view_events[event_id_to_index[action_id]]
                if isinstance(action_event, ActionEvent) and action_event.tool_call_id:
                    obs_idx = tool_call_to_obs_idx.get(action_event.tool_call_id)
                    if obs_idx is not None:
                        max_idx = max(max_idx, obs_idx)

            atomic_ranges.append((min_idx, max_idx))

        # Build manipulation indices that exclude atomic ranges
        return self._build_manipulation_indices_from_atomic_ranges(
            atomic_ranges, len(current_view_events)
        )
