from collections.abc import Sequence
from logging import getLogger

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.event import (
    Event,
    EventID,
    LLMConvertibleEvent,
    ObservationBaseEvent,
    ToolCallID,
)


logger = getLogger(__name__)


class ObservationUniquenessProperty(ViewPropertyBase):
    """At most one observation-like event per tool_call_id.

    Crash recovery can synthesize an ``AgentErrorEvent`` for an in-flight tool
    call and then the original ``ObservationEvent`` may still arrive late, so
    the view ends up with two observation-like events sharing a single
    ``tool_call_id``. Downstream LLM APIs (for example Anthropic tool use)
    require exactly one ``tool_result`` per ``tool_use``, and the strict
    pairing assumed by ``ToolCallMatchingProperty`` would otherwise raise
    ``KeyError`` during condensation.

    This property is registered ahead of ``ToolCallMatchingProperty`` so the
    duplicate is dropped before pairing logic runs.
    """

    def enforce(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],  # noqa: ARG002
    ) -> set[EventID]:
        """Drop any observation-like event whose ``tool_call_id`` has already
        been observed earlier in the view. The first occurrence wins because
        the agent has likely already seen it.
        """
        events_to_remove: set[EventID] = set()
        seen_tool_call_ids: set[ToolCallID] = set()

        for event in current_view_events:
            if isinstance(event, ObservationBaseEvent):
                if event.tool_call_id in seen_tool_call_ids:
                    events_to_remove.add(event.id)
                else:
                    seen_tool_call_ids.add(event.tool_call_id)

        return events_to_remove

    def manipulation_indices(
        self,
        current_view_events: list[LLMConvertibleEvent],
    ) -> ManipulationIndices:
        """This property does not restrict manipulation indices. If a duplicate
        observation-like event slips past ``enforce``, log a warning so the
        regression is visible without crashing condensation.
        """
        seen_tool_call_ids: set[ToolCallID] = set()

        for event in current_view_events:
            if isinstance(event, ObservationBaseEvent):
                if event.tool_call_id in seen_tool_call_ids:
                    logger.warning(
                        "Duplicate observation-like event for tool_call_id=%s",
                        event.tool_call_id,
                    )
                else:
                    seen_tool_call_ids.add(event.tool_call_id)

        return ManipulationIndices.complete(current_view_events)
