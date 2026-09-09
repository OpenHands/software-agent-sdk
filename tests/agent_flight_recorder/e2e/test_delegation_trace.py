from datetime import datetime

from flight_recorder.gui.main_window import MainWindow
from flight_recorder.models.envelopes import Record
from flight_recorder.services.repository import TraceRepository


def test_parallel_delegation_trace_is_navigable(trace_index, qtbot) -> None:
    def started(
        record_id: str,
        sequence: int,
        span_id: str,
        parent_span_id: str | None = None,
    ) -> Record:
        return Record(
            record_id=record_id,
            producer_id="producer",
            trace_id="trace",
            span_id=span_id,
            parent_span_id=parent_span_id,
            agent_span_id=span_id,
            sequence=sequence,
            observed_at=datetime(2026, 9, 1, 12, 0, sequence),
            kind="agent.started",
            payload={"task": f"Work for {span_id}"},
        )

    trace_index.add_records(
        [
            started("parent-start", 1, "parent"),
            started("child-a-start", 2, "child-a", "parent"),
            started("child-b-start", 3, "child-b", "parent"),
        ]
    )
    window = MainWindow(repository=TraceRepository(trace_index), trace_id="trace")
    qtbot.addWidget(window)

    window.navigate_to_agent("child-b")

    assert window.selection.current_selection().span_id == "child-b"
    assert window.tree.currentIndex().data() == "child-b"
