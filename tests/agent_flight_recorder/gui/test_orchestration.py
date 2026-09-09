from datetime import datetime

from flight_recorder.gui.inspector.widget import InspectorWidget
from flight_recorder.gui.models.trace_tree import TraceTreeModel
from flight_recorder.gui.timeline.items import TimelineSpanItem
from flight_recorder.gui.timeline.layout import project_spans
from flight_recorder.models.envelopes import (
    Provenance,
    RelationshipStatus,
    Span,
    TokenUsage,
)
from flight_recorder.models.view_models import (
    AgentDetail,
    AgentRelationship,
    TimelineView,
)


def agent_span(
    span_id: str,
    *,
    parent_span_id: str | None = None,
    status: RelationshipStatus = RelationshipStatus.VERIFIED,
) -> Span:
    return Span(
        span_id=span_id,
        trace_id="trace",
        span_type="agent",
        name=span_id,
        parent_span_id=parent_span_id,
        agent_span_id=span_id,
        relationship_status=status,
        start_sequence=1 if parent_span_id is None else 2,
        end_sequence=6,
        started_at=datetime(2026, 9, 1, 12),
        ended_at=datetime(2026, 9, 1, 12, 0, 5),
        status="completed",
    )


def test_orchestration_tree_nests_agents_and_labels_lineage() -> None:
    parent = agent_span("parent")
    child = agent_span(
        "child",
        parent_span_id="parent",
        status=RelationshipStatus.INFERRED,
    )

    model = TraceTreeModel(
        TimelineView(trace_id="trace", records=(), spans=(parent, child))
    )

    assert model.item(0).text() == "parent"
    assert model.item(0).child(0).text() == "child"
    assert "inferred" in model.item(0).child(0).toolTip().lower()


def test_timeline_and_inspector_show_lineage_and_handoff(qtbot) -> None:
    child = agent_span(
        "child",
        parent_span_id="parent",
        status=RelationshipStatus.UNRESOLVED,
    )
    item = TimelineSpanItem(project_spans([child], current_sequence=6)[0])
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    detail = AgentDetail(
        relationship=AgentRelationship(
            parent_span_id="parent",
            child_span_id="child",
            status=RelationshipStatus.UNRESOLVED,
            provenance=Provenance(),
        ),
        handoff={"task": "Inspect failure"},
        returned_result="Found root cause",
        duration_seconds=5,
        usage=TokenUsage(prompt_tokens=12),
        cost=0.08,
    )

    inspector.show_agent(detail)

    assert "unresolved" in item.toolTip().lower()
    assert "Inspect failure" in inspector.summary.text()
    assert "Found root cause" in inspector.summary.text()
    assert "12" in inspector.summary.text()
    assert inspector.tabText(inspector.indexOf(inspector.explanation)) == "Explanation"
    explanation = inspector.explanation.text()
    assert "Agent lifecycle" in explanation
    assert "delegated agent" in explanation
    assert "unresolved" in explanation
    assert "5.000 seconds" in explanation
    assert "Found root cause" in explanation
    assert "Prompt tokens: 12" in explanation
    assert "captured evidence" in explanation
