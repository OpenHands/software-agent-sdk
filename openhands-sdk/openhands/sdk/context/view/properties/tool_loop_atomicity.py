"""Property for ensuring tool loops remain atomic."""

from openhands.sdk.context.view.event_mappings import EventMappings
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

    def _identify_tool_loops(
        self, events: list[Event]
    ) -> list[list[EventID]]:
        """Identify all tool loops in the event sequence.

        Returns:
            List of tool loops, where each tool loop is a list of EventIDs
        """
        mappings = EventMappings.from_events(events)

        # Build batch ranges with metadata
        batch_ranges: list[tuple[int, int, bool, list[EventID]]] = []

        for llm_response_id, action_ids in mappings.batches.items():
            # Get indices for all actions in this batch
            indices = [mappings.event_id_to_index[aid] for aid in action_ids]
            min_idx = min(indices)
            max_idx = max(indices)

            # Check if any action in this batch has thinking blocks
            has_thinking = False
            for action_id in action_ids:
                idx = mappings.event_id_to_index[action_id]
                event = events[idx]
                if isinstance(event, ActionEvent) and event.thinking_blocks:
                    has_thinking = True
                    break

            batch_ranges.append((min_idx, max_idx, has_thinking, action_ids))

        # Sort batch ranges by min_idx
        batch_ranges.sort(key=lambda x: x[0])

        # Identify tool loops
        tool_loops: list[list[EventID]] = []

        i = 0
        while i < len(batch_ranges):
            min_idx, max_idx, has_thinking, action_ids = batch_ranges[i]

            if has_thinking:
                # Start of a tool loop - collect all event IDs in this loop
                loop_event_ids: list[EventID] = list(action_ids)
                loop_end = max_idx

                # Collect observation events between batches
                j = i + 1
                while j < len(batch_ranges):
                    next_min, next_max, _, next_action_ids = batch_ranges[j]

                    # Check if there are only ActionEvents/ObservationEvents between
                    all_action_or_obs = True
                    intermediate_ids: list[EventID] = []

                    for idx in range(loop_end + 1, next_min):
                        event = events[idx]
                        if isinstance(event, (ActionEvent, ObservationBaseEvent)):
                            intermediate_ids.append(event.id)
                        else:
                            all_action_or_obs = False
                            break

                    if all_action_or_obs:
                        # Extend the tool loop
                        loop_event_ids.extend(intermediate_ids)
                        loop_event_ids.extend(next_action_ids)
                        loop_end = next_max
                        j += 1
                    else:
                        # Tool loop ends here
                        break

                # Also collect any observation events after the last batch in the loop
                for idx in range(max_idx + 1, min(loop_end + 1, len(events))):
                    event = events[idx]
                    if isinstance(event, ObservationBaseEvent):
                        if event.id not in loop_event_ids:
                            loop_event_ids.append(event.id)
                    elif not isinstance(event, ActionEvent):
                        break

                tool_loops.append(loop_event_ids)
                i = j
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
        mappings = EventMappings.from_events(current_view_events)

        # Build batch ranges with metadata
        batch_ranges: list[tuple[int, int, bool]] = []

        for llm_response_id, action_ids in mappings.batches.items():
            # Get indices for all actions in this batch
            indices = [mappings.event_id_to_index[aid] for aid in action_ids]
            min_idx = min(indices)
            max_idx = max(indices)

            # Check if any action in this batch has thinking blocks
            has_thinking = False
            for action_id in action_ids:
                idx = mappings.event_id_to_index[action_id]
                event = current_view_events[idx]
                if isinstance(event, ActionEvent) and event.thinking_blocks:
                    has_thinking = True
                    break

            batch_ranges.append((min_idx, max_idx, has_thinking))

        # Sort batch ranges by min_idx
        batch_ranges.sort(key=lambda x: x[0])

        # Identify tool loop ranges
        tool_loop_ranges: list[tuple[int, int]] = []

        i = 0
        while i < len(batch_ranges):
            min_idx, max_idx, has_thinking = batch_ranges[i]

            if has_thinking:
                # Start of a tool loop
                loop_start = min_idx
                loop_end = max_idx

                # Scan forward through consecutive action/observation batches
                j = i + 1
                while j < len(batch_ranges):
                    next_min, next_max, _ = batch_ranges[j]

                    # Check if there are only ActionEvents/ObservationEvents between
                    # current loop_end and next_min
                    all_action_or_obs = True
                    for idx in range(loop_end + 1, next_min):
                        event = current_view_events[idx]
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

                tool_loop_ranges.append((loop_start, loop_end))
                i = j
            else:
                i += 1

        # Build set of all valid manipulation indices
        # We can manipulate at any index not inside a tool loop range
        valid_indices = set(range(len(current_view_events) + 1))

        # Remove indices that fall within tool loop ranges
        for loop_start, loop_end in tool_loop_ranges:
            # Cannot insert/remove within the tool loop (exclusive of start boundary)
            for idx in range(loop_start + 1, loop_end + 1):
                valid_indices.discard(idx)

        return ManipulationIndices(valid_indices)
