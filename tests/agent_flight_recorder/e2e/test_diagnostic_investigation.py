from datetime import datetime

from flight_recorder.gui.main_window import MainWindow
from flight_recorder.models.envelopes import Record
from flight_recorder.services.diagnostics import DiagnosticService
from flight_recorder.services.repository import TraceRepository


def test_unproductive_run_finding_opens_its_evidence(trace_index, qtbot) -> None:
    records = [
        Record(
            record_id=f"repeat-{index}",
            producer_id="producer",
            trace_id="trace",
            sequence=index,
            observed_at=datetime(2026, 9, 1, 12),
            kind="tool.request",
            payload={"tool": "terminal", "command": "pytest"},
        )
        for index in (1, 2)
    ]
    trace_index.add_records(records)
    findings = DiagnosticService(trace_index).analyze("trace")
    window = MainWindow(repository=TraceRepository(trace_index), trace_id="trace")
    qtbot.addWidget(window)
    window.set_findings(findings)

    window.navigate_to_finding(0)

    assert window.selection.current_selection().record_id == "repeat-1"
    assert window.findings_model.finding_at(0).detector_id == "repeated-tool-call"
