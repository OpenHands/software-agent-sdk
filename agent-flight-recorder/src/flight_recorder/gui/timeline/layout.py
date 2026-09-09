from collections.abc import Iterable
from dataclasses import dataclass

from flight_recorder.models.envelopes import RelationshipStatus, Span


@dataclass(frozen=True)
class SpanLayout:
    span_id: str
    lane: int
    start: int
    end: int
    incomplete: bool
    relationship_status: RelationshipStatus
    depth: int


def project_spans(
    spans: Iterable[Span],
    *,
    current_sequence: int,
    visible_range: tuple[int, int] | None = None,
) -> list[SpanLayout]:
    ordered = sorted(spans, key=lambda span: (span.start_sequence, span.span_id))
    lane_ids = list(
        dict.fromkeys(span.agent_span_id or span.span_id for span in ordered)
    )
    lanes = {lane_id: index for index, lane_id in enumerate(lane_ids)}
    spans_by_id = {span.span_id: span for span in ordered}
    depth_by_id: dict[str, int] = {}

    def depth(span: Span) -> int:
        cached = depth_by_id.get(span.span_id)
        if cached is not None:
            return cached
        lineage = []
        parent_id = span.parent_span_id
        visited = {span.span_id}
        while parent_id is not None and parent_id not in visited:
            cached_parent = depth_by_id.get(parent_id)
            if cached_parent is not None:
                result = cached_parent + len(lineage) + 1
                break
            parent = spans_by_id.get(parent_id)
            if parent is None:
                result = len(lineage)
                break
            lineage.append(parent_id)
            visited.add(parent_id)
            parent_id = parent.parent_span_id
        else:
            result = len(lineage)
        depth_by_id[span.span_id] = result
        return result

    projected = []
    for span in ordered:
        end = span.end_sequence or current_sequence
        if visible_range is not None:
            visible_start, visible_end = visible_range
            if end < visible_start or span.start_sequence > visible_end:
                continue
        projected.append(
            SpanLayout(
                span_id=span.span_id,
                lane=lanes[span.agent_span_id or span.span_id],
                start=span.start_sequence,
                end=max(span.start_sequence, end),
                incomplete=span.end_sequence is None,
                relationship_status=span.relationship_status,
                depth=depth(span),
            )
        )
    return projected
