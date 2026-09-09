from datetime import datetime, timedelta

from flight_recorder.models.envelopes import ProvenanceKind, Record
from flight_recorder.models.view_models import RunQuery
from flight_recorder.services.repository import TraceRepository


def record(
    record_id: str,
    trace_id: str,
    sequence: int,
    kind: str,
    *,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    agent_span_id: str | None = None,
    observed_seconds: float | None = None,
    payload: dict | None = None,
) -> Record:
    return Record(
        record_id=record_id,
        producer_id="producer",
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_span_id=agent_span_id,
        sequence=sequence,
        observed_at=datetime(2026, 9, 1, 12)
        + timedelta(
            seconds=observed_seconds if observed_seconds is not None else sequence
        ),
        kind=kind,
        payload=payload or {},
    )


def test_repository_filters_runs_and_preserves_generic_records(trace_index) -> None:
    trace_index.add_records(
        [
            record(
                "a-start",
                "trace-a",
                1,
                "run.started",
                payload={"repository": "sdk", "model": "test-model"},
            ),
            record("future", "trace-a", 2, "future.activity"),
            record("a-finish", "trace-a", 3, "run.finished"),
            record("b-start", "trace-b", 1, "run.started"),
        ]
    )
    repository = TraceRepository(trace_index)

    completed = repository.list_runs(RunQuery(status="completed", repository="sdk"))

    assert [run.trace_id for run in completed] == ["trace-a"]
    assert repository.get_timeline("trace-a").records[1].kind == "future.activity"


def test_repository_projects_span_detail_and_workspace_changes(trace_index) -> None:
    trace_index.add_records(
        [
            record("start", "trace", 1, "tool.request", span_id="tool-span"),
            record(
                "change",
                "trace",
                2,
                "workspace.diff",
                span_id="tool-span",
                payload={"path": "src/app.py", "content_hash": "abc", "size": 12},
            ),
            record("finish", "trace", 3, "tool.result", span_id="tool-span"),
        ]
    )
    repository = TraceRepository(trace_index)

    detail = repository.get_span("trace", "tool-span")
    changes = repository.get_workspace_changes("trace", "tool-span")

    assert detail.span.start_sequence == 1
    assert detail.span.end_sequence == 3
    assert [item.record_id for item in detail.records] == ["start", "change", "finish"]
    assert [(item.path, item.created_sequence) for item in changes] == [
        ("src/app.py", 2)
    ]


def test_agent_span_requires_explicit_finish_record(trace_index) -> None:
    trace_index.add_records(
        [
            record(
                "start",
                "trace",
                1,
                "agent.started",
                span_id="agent",
                agent_span_id="agent",
            ),
            record(
                "activity",
                "trace",
                2,
                "conversation.event",
                span_id="agent",
                agent_span_id="agent",
            ),
        ]
    )
    repository = TraceRepository(trace_index)

    active = repository.get_span("trace", "agent").span

    assert active.status == "incomplete"
    assert active.end_sequence is None
    assert active.ended_at is None

    trace_index.add_records(
        [
            record(
                "finish",
                "trace",
                3,
                "agent.finished",
                span_id="agent",
                agent_span_id="agent",
            )
        ]
    )

    finished = repository.get_span("trace", "agent").span

    assert finished.status == "completed"
    assert finished.end_sequence == 3
    assert finished.ended_at == datetime(2026, 9, 1, 12, 0, 3)


def test_repository_projects_legacy_llm_exchange_without_mutating_index(
    trace_index,
) -> None:
    legacy = record(
        "completion",
        "trace",
        1,
        "llm.response",
        payload={
            "filename": "completion.json",
            "completion": {
                "input": [{"type": "message", "role": "user"}],
                "response": {"id": "response-1"},
                "timestamp": datetime(2026, 9, 1, 12, 0, 5).timestamp(),
                "latency_sec": 1.5,
            },
        },
    )
    trace_index.add_records([legacy])
    repository = TraceRepository(trace_index)

    timeline = repository.get_timeline("trace")

    assert [item.kind for item in timeline.records] == [
        "llm.request",
        "llm.response",
    ]
    assert [item.record_id for item in timeline.records] == [
        "completion/request",
        "completion/response",
    ]
    assert {item.llm_call_id for item in timeline.records} == {"completion"}
    assert timeline.records[0].payload["request"]["input"][0]["role"] == "user"
    assert timeline.records[1].payload["response"]["id"] == "response-1"
    assert timeline.records[0].source_timestamp == datetime(
        2026, 9, 1, 12, 0, 3, 500000
    )
    assert timeline.records[1].source_timestamp == datetime(2026, 9, 1, 12, 0, 5)
    assert all(
        item.provenance.kind is ProvenanceKind.DERIVED for item in timeline.records
    )
    assert repository.get_record("trace", "completion/request") == timeline.records[0]
    assert trace_index.iter_records("trace") == [legacy]


def test_primary_agent_duration_excludes_recorder_shutdown_idle_time(
    trace_index,
) -> None:
    trace_index.add_records(
        [
            record("run-start", "trace", 1, "run.started", observed_seconds=0),
            record(
                "root-start",
                "trace",
                2,
                "agent.started",
                span_id="root",
                agent_span_id="root",
                observed_seconds=0,
                payload={"name": "Primary agent"},
            ),
            record(
                "child-start",
                "trace",
                3,
                "agent.started",
                span_id="child",
                parent_span_id="root",
                agent_span_id="child",
                observed_seconds=5,
            ),
            record(
                "child-finish",
                "trace",
                4,
                "agent.finished",
                span_id="child",
                parent_span_id="root",
                agent_span_id="child",
                observed_seconds=20,
            ),
            record(
                "root-activity",
                "trace",
                5,
                "conversation.event",
                span_id="root",
                agent_span_id="root",
                observed_seconds=30,
            ),
            record(
                "root-finish",
                "trace",
                6,
                "agent.finished",
                span_id="root",
                agent_span_id="root",
                observed_seconds=3600,
            ),
            record(
                "run-finish",
                "trace",
                7,
                "run.finished",
                observed_seconds=3600,
                payload={"stop_reason": "recorder_closed"},
            ),
        ]
    )
    repository = TraceRepository(trace_index)

    assert repository.get_agent_detail("trace", "root").duration_seconds == 30
    assert repository.get_agent_detail("trace", "child").duration_seconds == 15
