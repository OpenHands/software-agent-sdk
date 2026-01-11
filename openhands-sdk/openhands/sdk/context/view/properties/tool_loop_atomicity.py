"""Property for ensuring tool loops remain atomic."""

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.event.base import Event, LLMConvertibleEvent
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import ObservationBaseEvent
from openhands.sdk.event.types import EventID


class ToolLoopAtomicityProperty(ViewPropertyBase):
    """Ensures that tool loops (thinking blocks + tool calls) remain atomic units.

    Claude API requires that thinking blocks stay with their associated tool calls.
    A tool loop is:
    - An initial batch containing thinking blocks (ActionEvents with non-empty thinking_blocks)
    - All subsequent consecutive ActionEvent/ObservationEvent batches
    - Terminated by the first non-ActionEvent/ObservationEvent
    """

    def _build_batch_ranges(
        self,
        batches: dict[EventID, list[EventID]],
        events: list[Event],
        event_id_to_index: dict[EventID, int],
    ) -> list[tuple[int, int, bool, list[EventID]]]:
        """Build batch range metadata for tool loop detection.

        Args:
            batches: Mapping of llm_response_id to action event IDs
            events: Event sequence to analyze
            event_id_to_index: Mapping of event IDs to their indices

        Returns:
            List of tuples (min_idx, max_idx, has_thinking, action_ids) sorted by min_idx
        """
        batch_ranges: list[tuple[int, int, bool, list[EventID]]] = []

        for llm_response_id, action_ids in batches.items():
            # Get indices for all actions in this batch
            min_idx, max_idx = self._get_batch_extent(action_ids, event_id_to_index)

            # Check if any action in this batch has thinking blocks
            has_thinking = False
            for action_id in action_ids:
                idx = event_id_to_index[action_id]
                event = events[idx]
                if isinstance(event, ActionEvent) and event.thinking_blocks:
                    has_thinking = True
                    break

            batch_ranges.append((min_idx, max_idx, has_thinking, action_ids))

        # Sort batch ranges by min_idx
        batch_ranges.sort(key=lambda x: x[0])
        return batch_ranges

    def _scan_tool_loop_extent(
        self,
        start_idx: int,
        batch_ranges: list[tuple[int, int, bool, list[EventID]]],
        events: list[Event],
    ) -> tuple[int, int, int]:
        """Scan forward from a starting batch to find the full extent of a tool loop.

        Args:
            start_idx: Index in batch_ranges where the tool loop starts (must have has_thinking=True)
            batch_ranges: Sorted list of batch range tuples
            events: Event sequence being analyzed

        Returns:
            Tuple of (loop_start_event_idx, loop_end_event_idx, next_batch_idx)
            - loop_start_event_idx: Index of first event in the tool loop
            - loop_end_event_idx: Index of last event in the tool loop
            - next_batch_idx: Index in batch_ranges after this loop ends
        """
        min_idx, max_idx, has_thinking, _ = batch_ranges[start_idx]

        if not has_thinking:
            raise ValueError("Tool loop must start with a batch containing thinking blocks")

        loop_start = min_idx
        loop_end = max_idx

        # Scan forward through consecutive action/observation batches
        j = start_idx + 1
        while j < len(batch_ranges):
            next_min, next_max, _, _ = batch_ranges[j]

            # Check if there are only ActionEvents/ObservationEvents between
            # current loop_end and next_min
            all_action_or_obs = True
            for idx in range(loop_end + 1, next_min):
                event = events[idx]
                if not isinstance(event, (ActionEvent, ObservationBaseEvent)):
                    all_action_or_obs = False
                    break

            if all_action_or_obs:
                # Extend the tool loop
                loop_end = next_max
                j += 1
            else:
                # Tool loop ends here
                break

        # Scan forward to include any trailing observations
        scan_idx = loop_end + 1
        while scan_idx < len(events):
            event = events[scan_idx]
            if isinstance(event, ObservationBaseEvent):
                loop_end = scan_idx
                scan_idx += 1
            elif isinstance(event, ActionEvent):
                # Another action - should have been caught by batch processing above
                break
            else:
                # Non-action/observation terminates the loop
                break

        return loop_start, loop_end, j

    def _identify_tool_loops(self, events: list[Event]) -> list[list[EventID]]:
        """Identify all tool loops in the event sequence.

        Returns:
            List of tool loops, where each tool loop is a list of EventIDs
        """
        batches = self._build_batches(events)
        event_id_to_index = self._build_event_id_to_index(events)

        # Build batch ranges with metadata using helper
        batch_ranges = self._build_batch_ranges(batches, events, event_id_to_index)

        # Identify tool loops
        tool_loops: list[list[EventID]] = []

        i = 0
        while i < len(batch_ranges):
            _, _, has_thinking, action_ids = batch_ranges[i]

            if has_thinking:
                # Use helper to find the full extent of this tool loop
                loop_start, loop_end, next_i = self._scan_tool_loop_extent(
                    i, batch_ranges, events
                )

                # Collect all event IDs within the loop range
                loop_event_ids: list[EventID] = []
                for idx in range(loop_start, loop_end + 1):
                    loop_event_ids.append(events[idx].id)

                tool_loops.append(loop_event_ids)
                i = next_i
            else:
                i += 1

        return tool_loops

    def enforce(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> set[EventID]:
        """Enforce tool loop atomicity by removing partially-present tool loops.

        If a tool loop is partially present in the view, all events from that
        tool loop are removed.

        Args:
            current_view_events: Events currently in the view
            all_events: All events in the conversation

        Returns:
            Set of EventIDs to remove from the current view
        """
        # Identify all tool loops in the complete conversation
        tool_loops = self._identify_tool_loops(all_events)

        # Build set of event IDs currently in view
        view_event_ids = {event.id for event in current_view_events}

        events_to_remove: set[EventID] = set()

        # Check each tool loop
        for loop_event_ids in tool_loops:
            # Count how many events from this loop are in the view
            events_in_view = [eid for eid in loop_event_ids if eid in view_event_ids]

            # If loop is partially present (some but not all events)
            if events_in_view and len(events_in_view) < len(loop_event_ids):
                # Remove all events from this loop that are in the view
                events_to_remove.update(events_in_view)

        return events_to_remove

    def manipulation_indices(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> ManipulationIndices:
        """Calculate manipulation indices that respect tool loop atomicity.

        Returns all indices outside of tool loop ranges.

        Args:
            current_view_events: Events currently in the view
            all_events: All events in the conversation

        Returns:
            ManipulationIndices with all valid manipulation points
        """
        batches = self._build_batches(current_view_events)
        event_id_to_index = self._build_event_id_to_index(current_view_events)

        # Build batch ranges with metadata using helper
        batch_ranges = self._build_batch_ranges(
            batches, current_view_events, event_id_to_index
        )

        # Identify tool loop ranges
        tool_loop_ranges: list[tuple[int, int]] = []

        i = 0
        while i < len(batch_ranges):
            _, _, has_thinking, _ = batch_ranges[i]

            if has_thinking:
                # Use helper to find the full extent of this tool loop
                loop_start, loop_end, next_i = self._scan_tool_loop_extent(
                    i, batch_ranges, current_view_events
                )
                tool_loop_ranges.append((loop_start, loop_end))
                i = next_i
            else:
                i += 1

        # Build manipulation indices that exclude tool loop ranges
        return self._build_manipulation_indices_from_atomic_ranges(
            tool_loop_ranges, len(current_view_events)
        )
