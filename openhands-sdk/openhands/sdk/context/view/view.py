from __future__ import annotations

from collections.abc import Sequence
from functools import cached_property
from logging import getLogger
from typing import overload

from pydantic import BaseModel, computed_field

from openhands.sdk.context.view.properties import ALL_PROPERTIES
from openhands.sdk.event import (
    Condensation,
    CondensationRequest,
    LLMConvertibleEvent,
)
from openhands.sdk.event.base import Event, EventID
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    ObservationBaseEvent,
)
from openhands.sdk.event.types import ToolCallID


logger = getLogger(__name__)


class View(BaseModel):
    """Linearly ordered view of events.

    Produced by a condenser to indicate the included events are ready to process as LLM
    input. Also contains fields with information from the condensation process to aid
    in deciding whether further condensation is needed.
    """

    events: list[LLMConvertibleEvent]

    unhandled_condensation_request: bool = False
    """Whether there is an unhandled condensation request in the view."""

    condensations: list[Condensation] = []
    """A list of condensations that were processed to produce the view."""

    def __len__(self) -> int:
        return len(self.events)

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def manipulation_indices(self) -> list[int]:
        """Return cached manipulation indices for this view's events.

        These indices represent boundaries between atomic units where events can be
        safely manipulated (inserted or forgotten). An atomic unit is either:
        - A tool loop: a sequence of batches starting with thinking blocks and
          continuing through all subsequent batches until a non-batch event
        - A batch of ActionEvents with the same llm_response_id and their
          corresponding ObservationBaseEvents (when not part of a tool loop)
        - A single event that is neither an ActionEvent nor an ObservationBaseEvent

        Tool loops are identified by thinking blocks and must remain atomic to
        preserve Claude API requirements that the final assistant message must
        have thinking blocks when thinking is enabled.

        The returned indices can be used for:
        - Inserting new events: any returned index is safe
        - Forgetting events: select a range between two consecutive indices

        Consecutive indices define atomic units that must stay together:
        - events[indices[i]:indices[i+1]] is an atomic unit

        Returns:
            Sorted list of indices representing atomic unit boundaries. Always
            includes 0 and len(events) as boundaries.
        """
        if not self.events:
            return [0]

        # Build mapping of llm_response_id -> list of event indices
        batches: dict[EventID, list[int]] = {}
        for idx, event in enumerate(self.events):
            if isinstance(event, ActionEvent):
                llm_response_id = event.llm_response_id
                if llm_response_id not in batches:
                    batches[llm_response_id] = []
                batches[llm_response_id].append(idx)

        # Build mapping of tool_call_id -> observation indices
        observation_indices: dict[ToolCallID, int] = {}
        for idx, event in enumerate(self.events):
            if (
                isinstance(event, ObservationBaseEvent)
                and event.tool_call_id is not None
            ):
                observation_indices[event.tool_call_id] = idx

        # For each batch, find the range of indices that includes all actions
        # and their corresponding observations, and track if batch has thinking blocks
        batch_ranges: list[tuple[int, int, bool]] = []
        for llm_response_id, action_indices in batches.items():
            min_idx = min(action_indices)
            max_idx = max(action_indices)

            # Check if this batch has thinking blocks (only first action has them)
            first_action = self.events[min_idx]
            has_thinking = (
                isinstance(first_action, ActionEvent)
                and len(first_action.thinking_blocks) > 0
            )

            # Extend the range to include all corresponding observations
            for action_idx in action_indices:
                action_event = self.events[action_idx]
                if (
                    isinstance(action_event, ActionEvent)
                    and action_event.tool_call_id is not None
                ):
                    if action_event.tool_call_id in observation_indices:
                        obs_idx = observation_indices[action_event.tool_call_id]
                        max_idx = max(max_idx, obs_idx)

            batch_ranges.append((min_idx, max_idx, has_thinking))

        # Sort batch ranges by start index for tool loop detection
        batch_ranges.sort(key=lambda x: x[0])

        # Identify tool loops: A tool loop starts with a batch that has thinking
        # blocks and continues through all subsequent batches until we hit a
        # non-ActionEvent/ObservationEvent (like a user MessageEvent).
        tool_loop_ranges: list[tuple[int, int]] = []
        if batch_ranges:
            i = 0
            while i < len(batch_ranges):
                min_idx, max_idx, has_thinking = batch_ranges[i]

                # If this batch has thinking blocks, start a tool loop
                if has_thinking:
                    loop_start = min_idx
                    loop_end = max_idx

                    # Continue through ALL subsequent batches until we hit
                    # a non-batch event
                    j = i + 1
                    while j < len(batch_ranges):
                        next_min, next_max, _ = batch_ranges[j]

                        # Check if there's a non-batch event between current
                        # and next batch
                        has_non_batch_between = False
                        for k in range(loop_end + 1, next_min):
                            event = self.events[k]
                            if not isinstance(
                                event, (ActionEvent, ObservationBaseEvent)
                            ):
                                has_non_batch_between = True
                                break

                        if has_non_batch_between:
                            # Tool loop ends before this non-batch event
                            break

                        # Include this batch in the tool loop
                        loop_end = max(loop_end, next_max)
                        j += 1

                    tool_loop_ranges.append((loop_start, loop_end))
                    i = j
                else:
                    i += 1

        # Merge batch ranges that are part of tool loops
        # Create a mapping of batch index ranges to whether they're in a tool loop
        merged_ranges: list[tuple[int, int]] = []

        if tool_loop_ranges:
            # Add tool loop ranges as atomic units
            merged_ranges.extend(tool_loop_ranges)

            # Add non-tool-loop batch ranges
            tool_loop_indices = set()
            for loop_start, loop_end in tool_loop_ranges:
                tool_loop_indices.update(range(loop_start, loop_end + 1))

            for min_idx, max_idx, has_thinking in batch_ranges:
                # Only add if not already covered by a tool loop
                if min_idx not in tool_loop_indices:
                    merged_ranges.append((min_idx, max_idx))
        else:
            # No tool loops, just use regular batch ranges
            merged_ranges = [(min_idx, max_idx) for min_idx, max_idx, _ in batch_ranges]

        # Start with all possible indices (subtractive approach)
        result_indices = set(range(len(self.events) + 1))

        # Remove indices inside merged ranges (keep only boundaries)
        for min_idx, max_idx in merged_ranges:
            # Remove interior indices, keeping min_idx and max_idx+1 as boundaries
            for idx in range(min_idx + 1, max_idx + 1):
                result_indices.discard(idx)

        return sorted(result_indices)

    # To preserve list-like indexing, we ideally support slicing and position-based
    # indexing. The only challenge with that is switching the return type based on the
    # input type -- we can mark the different signatures for MyPy with `@overload`
    # decorators.

    @overload
    def __getitem__(self, key: slice) -> list[LLMConvertibleEvent]: ...

    @overload
    def __getitem__(self, key: int) -> LLMConvertibleEvent: ...

    def __getitem__(
        self, key: int | slice
    ) -> LLMConvertibleEvent | list[LLMConvertibleEvent]:
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            return [self[i] for i in range(start, stop, step)]
        elif isinstance(key, int):
            return self.events[key]
        else:
            raise ValueError(f"Invalid key type: {type(key)}")

    def find_next_manipulation_index(self, threshold: int) -> int:
        """Find the smallest manipulation index greater than or equal to a threshold.

        This is a helper method for condensation logic that needs to find safe
        boundaries for forgetting events. Uses the cached manipulation_indices property.

        Args:
            threshold: The threshold value to compare against

        Returns:
            The smallest manipulation index >= threshold, or the threshold itself
            if no such index exists
        """
        for idx in self.manipulation_indices:
            if idx >= threshold:
                return idx
        return threshold

    @staticmethod
    def unhandled_condensation_request_exists(
        events: Sequence[Event],
    ) -> bool:
        """Check if there is an unhandled condensation request in the list of events.

        An unhandled condensation request is defined as a CondensationRequest event
        that appears after the most recent Condensation event in the list.
        """
        for event in reversed(events):
            if isinstance(event, Condensation):
                return False
            if isinstance(event, CondensationRequest):
                return True
        return False

    @staticmethod
    def enforce_properties(
        current_view_events: list[LLMConvertibleEvent], all_events: Sequence[Event]
    ) -> list[LLMConvertibleEvent]:
        """Enforce all properties on the list of current view events.

        Repeatedly applies each property's enforcement mechanism until the list of view
        events reaches a stable state.

        Since enforcement is intended as a fallback to inductively maintaining the
        properties via the associated manipulation indices, any time a property must be
        enforced a warning is logged.
        """
        for property in ALL_PROPERTIES:
            events_to_forget = property.enforce(current_view_events, all_events)
            if events_to_forget:
                logger.warning(
                    f"Property {property.__class__} enforced, "
                    f"{len(events_to_forget)} events dropped."
                )
                return View.enforce_properties(
                    [
                        event
                        for event in current_view_events
                        if event.id not in events_to_forget
                    ],
                    all_events,
                )
        return current_view_events

    @staticmethod
    def from_events(events: Sequence[Event]) -> View:
        """Create a view from a list of events, respecting the semantics of any
        condensation events.
        """
        output: list[LLMConvertibleEvent] = []
        condensations: list[Condensation] = []

        # Generate the LLMConvertibleEvent objects the agent can send to the LLM by
        # removing un-sendable events and applying condensations in order.
        for event in events:
            # By the time we come across a Condensation event, the output list should
            # already reflect the events seen by the agent up to that point. We can
            # therefore apply the condensation semantics directly to the output list.
            if isinstance(event, Condensation):
                condensations.append(event)
                output = event.apply(output)

            elif isinstance(event, LLMConvertibleEvent):
                output.append(event)

            # If the event isn't related to condensation and isn't LLMConvertible, it
            # should not be in the resulting view. Examples include certain internal
            # events used for state tracking that the LLM does not need to see -- see,
            # for example, ConversationStateUpdateEvent, PauseEvent, and (relevant here)
            # CondensationRequest.
            else:
                logger.debug(
                    f"Skipping non-LLMConvertibleEvent of type {type(event)} "
                    f"in View.from_events"
                )

        output = View.enforce_properties(output, events)

        return View(
            events=output,
            unhandled_condensation_request=View.unhandled_condensation_request_exists(
                events
            ),
            condensations=condensations,
        )
