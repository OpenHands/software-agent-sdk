from datetime import datetime

from flight_recorder.gui.timeline.layout import project_spans
from flight_recorder.models.envelopes import Record, Span
from flight_recorder.services.repository import TraceRepository


def agent_span(span_id: str, start: int, end: int) -> Span:
    return Span(
        span_id=span_id,
        trace_id="trace",
        span_type="agent",
        name=span_id,
        parent_span_id="parent",
        agent_span_id=span_id,
        start_sequence=start,
        end_sequence=end,
        started_at=datetime(2026, 9, 1, 12),
        ended_at=datetime(2026, 9, 1, 12, 0, end),
        status="completed",
    )


def test_parallel_agent_branches_have_stable_distinct_lanes() -> None:
    spans = [agent_span("child-b", 2, 6), agent_span("child-a", 2, 5)]

    projected = project_spans(spans, current_sequence=6)

    assert [(item.span_id, item.lane) for item in projected] == [
        ("child-a", 0),
        ("child-b", 1),
    ]


def test_repository_aggregates_metrics_per_agent(trace_index) -> None:
    def metrics(
        record_id: str, sequence: int, agent_span_id: str, tokens: int
    ) -> Record:
        return Record(
            record_id=record_id,
            producer_id="producer",
            trace_id="trace",
            agent_span_id=agent_span_id,
            sequence=sequence,
            observed_at=datetime(2026, 9, 1, 12),
            kind="metrics.snapshot",
            payload={
                "usage_to_metrics": {
                    record_id: {
                        "accumulated_token_usage": {"prompt_tokens": tokens},
                        "accumulated_cost": tokens / 100,
                    }
                }
            },
        )

    trace_index.add_records(
        [
            metrics("parent-metrics", 1, "parent", 10),
            metrics("child-metrics", 2, "child", 4),
        ]
    )
    repository = TraceRepository(trace_index)

    usage, cost = repository.get_agent_usage("trace", "child")

    assert usage.prompt_tokens == 4
    assert cost == 0.04
