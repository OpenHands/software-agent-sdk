from typing import Literal

from flight_recorder.gui.main_window import MainWindow
from flight_recorder.gui.models.findings import FindingsModel
from flight_recorder.models.envelopes import Finding


def finding(
    finding_id: str,
    severity: Literal["info", "warning", "error"],
    category: str,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        trace_id="trace",
        origin="rule",
        detector_id=finding_id,
        category=category,
        severity=severity,
        confidence=1,
        title=finding_id,
        explanation="Evidence-backed explanation",
        recommendation="Change approach",
        evidence_ids=(f"evidence-{finding_id}",),
    )


def test_findings_model_filters_severity_and_category() -> None:
    model = FindingsModel(
        [finding("loop", "warning", "loop"), finding("tool", "error", "tool")]
    )

    model.set_filters(severity="error", category=None)

    assert model.rowCount() == 1
    assert model.finding_at(0).finding_id == "tool"
    assert model.data(model.index(0, 0)) == "error"
    assert model.evidence_at(0) == ("evidence-tool",)


def test_finding_navigation_selects_openable_evidence(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_findings([finding("loop", "warning", "loop")])

    window.navigate_to_finding(0)

    assert window.selection.current_selection().record_id == "evidence-loop"
    assert "Evidence-backed explanation" in window.inspector.summary.text()
    explanation = window.inspector.explanation.text()
    assert "Diagnostic finding" in explanation
    assert "deterministic recorder rule" in explanation
    assert "Confidence: 100%" in explanation
    assert "evidence-loop" in explanation
