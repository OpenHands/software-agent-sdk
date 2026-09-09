from datetime import datetime

from flight_recorder.adapter.propagation import AgentTraceContext
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import Provenance, ProvenanceKind, Record
from flight_recorder.recorder import Recorder
from flight_recorder.services.repository import TraceRepository

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.llm import Metrics


def lineage_record(
    record_id: str,
    sequence: int,
    span_id: str,
    *,
    parent_span_id: str | None = None,
    provenance: Provenance | None = None,
) -> Record:
    return Record(
        record_id=record_id,
        producer_id="producer",
        trace_id="trace",
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_span_id=span_id,
        sequence=sequence,
        observed_at=datetime(2026, 9, 1, 12),
        kind="agent.started",
        provenance=provenance or Provenance(),
    )


def test_child_trace_context_preserves_trace_and_parent_agent() -> None:
    parent = AgentTraceContext(trace_id="trace", agent_span_id="parent")

    child = parent.child("child")

    assert child.trace_id == "trace"
    assert child.agent_span_id == "child"
    assert child.parent_agent_span_id == "parent"


def test_repository_labels_verified_inferred_and_unresolved_lineage(
    trace_index,
) -> None:
    trace_index.add_records(
        [
            lineage_record("parent", 1, "parent"),
            lineage_record("verified", 2, "verified", parent_span_id="parent"),
            lineage_record(
                "inferred",
                3,
                "inferred",
                parent_span_id="parent",
                provenance=Provenance(
                    kind=ProvenanceKind.DERIVED,
                    source_ids=("parent",),
                ),
            ),
            lineage_record("unresolved", 4, "unresolved", parent_span_id="missing"),
        ]
    )
    repository = TraceRepository(trace_index)

    relationships = repository.get_agent_relationships("trace")

    assert [(item.child_span_id, item.status.value) for item in relationships] == [
        ("verified", "verified"),
        ("inferred", "inferred"),
        ("unresolved", "unresolved"),
    ]


def test_nested_recorder_callbacks_use_unique_spans_and_capture_usage(tmp_path) -> None:
    index = TraceIndex(tmp_path / "trace.db")
    recorder = Recorder(tmp_path / "nested.afr", index)
    child = recorder.for_subagent(
        task_id="task_00000001",
        subagent_type="general-purpose",
        description="First level",
    )
    nested = child.for_subagent(
        task_id="task_00000001",
        subagent_type="general-purpose",
        description="Second level",
    )
    metrics = Metrics(model_name="test-model")
    metrics.add_cost(0.03)
    metrics.add_token_usage(7, 2, 0, 0, 64, "child-response")
    stats = ConversationStats(usage_to_metrics={"child": metrics})

    nested.finish_subagent(status="completed", result="done", error=None, stats=stats)
    child.finish_subagent(status="completed", result="done", error=None, stats=stats)
    recorder.close()

    starts = [
        record
        for record in index.iter_records(recorder.trace_id)
        if record.kind == "agent.started" and record.parent_span_id is not None
    ]
    assert len({record.span_id for record in starts}) == 2
    assert starts[1].parent_span_id == starts[0].span_id
    usage, cost = TraceRepository(index).get_agent_usage(
        recorder.trace_id, starts[1].span_id or ""
    )
    assert usage.prompt_tokens == 7
    assert cost == 0.03
