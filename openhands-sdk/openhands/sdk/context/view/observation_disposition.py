from collections.abc import Iterable
from dataclasses import dataclass

from openhands.sdk.event import Event, EventID, ObservationBaseEvent, ToolCallID


@dataclass(frozen=True)
class ObservationDisposition:
    tool_call_id: ToolCallID
    canonical_event_id: EventID
    out_of_view_event_ids: tuple[EventID, ...]


def get_observation_dispositions(
    events: Iterable[Event],
) -> tuple[ObservationDisposition, ...]:
    """Select the first observation-like outcome per tool call in input order."""
    outcomes: dict[ToolCallID, list[EventID]] = {}
    for event in events:
        if isinstance(event, ObservationBaseEvent):
            outcomes.setdefault(event.tool_call_id, []).append(event.id)

    return tuple(
        ObservationDisposition(
            tool_call_id=tool_call_id,
            canonical_event_id=event_ids[0],
            out_of_view_event_ids=tuple(event_ids[1:]),
        )
        for tool_call_id, event_ids in outcomes.items()
    )
