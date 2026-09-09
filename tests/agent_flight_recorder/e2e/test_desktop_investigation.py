from datetime import datetime, timedelta

from flight_recorder.gui.main_window import MainWindow
from flight_recorder.gui.models.run_table import RunTableModel
from flight_recorder.gui.models.trace_tree import TraceTreeModel
from flight_recorder.gui.timeline.scene import TimelineScene
from flight_recorder.gui.timeline.view import TimelineViewWidget
from flight_recorder.models.envelopes import Record
from flight_recorder.services.repository import TraceRepository


def test_desktop_opens_seeded_trace_for_investigation(qtbot, trace_index) -> None:
    started_at = datetime(2026, 9, 1, 12)
    records = [
        Record(
            record_id=record_id,
            producer_id="producer",
            trace_id="trace",
            span_id=span_id,
            agent_span_id="agent" if span_id else None,
            sequence=sequence,
            observed_at=started_at + timedelta(seconds=sequence),
            kind=kind,
        )
        for record_id, sequence, kind, span_id in (
            ("run-start", 1, "run.started", None),
            ("tool-start", 2, "tool.request", "tool-span"),
            ("tool-finish", 3, "tool.result", "tool-span"),
            ("run-finish", 4, "run.finished", None),
        )
    ]
    trace_index.add_records(records)

    window = MainWindow(repository=TraceRepository(trace_index), trace_id="trace")
    qtbot.addWidget(window)

    run_model = window.findChild(RunTableModel)
    tree_model = window.findChild(TraceTreeModel)
    timeline = window.findChild(TimelineViewWidget, "timeline")
    assert run_model is not None
    assert run_model.data(run_model.index(0, 1)) == "completed"
    assert tree_model is not None
    assert tree_model.rowCount() == 4
    assert timeline is not None
    scene = timeline.scene()
    assert isinstance(scene, TimelineScene)
    assert scene.span_at(sequence=2, lane=0) == "tool-span"
