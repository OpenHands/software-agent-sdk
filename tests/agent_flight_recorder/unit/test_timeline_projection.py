from datetime import datetime

from flight_recorder.gui.timeline.layout import project_spans
from flight_recorder.models.envelopes import Span


def make_span(
    span_id: str,
    agent_span_id: str,
    start: int,
    end: int | None,
) -> Span:
    return Span(
        span_id=span_id,
        trace_id="trace",
        span_type="tool",
        name=span_id,
        agent_span_id=agent_span_id,
        start_sequence=start,
        end_sequence=end,
        started_at=datetime(2026, 9, 1, 12),
        status="completed" if end is not None else "incomplete",
    )


def test_projection_assigns_stable_lanes_and_incomplete_bounds() -> None:
    spans = [
        make_span("late", "agent-b", 4, 6),
        make_span("early", "agent-a", 1, 3),
        make_span("active", "agent-a", 7, None),
    ]

    layout = project_spans(spans, current_sequence=10)

    assert [(item.span_id, item.lane) for item in layout] == [
        ("early", 0),
        ("late", 1),
        ("active", 0),
    ]
    assert layout[-1].start == 7
    assert layout[-1].end == 10
    assert layout[-1].incomplete is True


def test_projection_filters_to_visible_sequence_range() -> None:
    spans = [
        make_span("before", "agent-a", 1, 2),
        make_span("visible", "agent-b", 3, 8),
        make_span("after", "agent-c", 9, 10),
    ]

    layout = project_spans(
        spans,
        current_sequence=10,
        visible_range=(4, 7),
    )

    assert [item.span_id for item in layout] == ["visible"]
