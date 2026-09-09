import json

from flight_recorder.gui.inspector.explanation import (
    explain_agent,
    explain_finding,
    explain_record,
    explain_run,
)
from flight_recorder.gui.inspector.metrics import MetricsWidget
from flight_recorder.gui.inspector.summary import (
    StyledSummary,
    SummaryTone,
    format_styled_record_summary,
)
from flight_recorder.models.envelopes import Finding, Record
from flight_recorder.models.view_models import AgentDetail, RunSummary
from PySide6.QtGui import QColor, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
)


SUMMARY_TONE_COLORS = {
    SummaryTone.OPENHANDS: ("#e5eff8", "#174a70"),
    SummaryTone.USER: ("#e5f5eb", "#20583a"),
    SummaryTone.AGENT: ("#fff0dc", "#704315"),
}


class InspectorTextView(QPlainTextEdit):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802
        self.setPlainText(text)
        self.setExtraSelections([])

    def set_styled_summary(self, summary: StyledSummary) -> None:
        self.setPlainText(summary.text)
        selections = []
        for highlight in summary.highlights:
            background, foreground = SUMMARY_TONE_COLORS[highlight.tone]
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(self.document())
            cursor.setPosition(highlight.start)
            cursor.setPosition(highlight.end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selection.format.setBackground(QColor(background))
            selection.format.setForeground(QColor(foreground))
            selections.append(selection)
        self.setExtraSelections(selections)


class InspectorWidget(QTabWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inspector")
        self.setAccessibleName("Activity inspector")
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        self.summary = InspectorTextView("No selection")
        self.explanation = InspectorTextView(
            "Select a run, agent, finding, or activity to see its explanation."
        )
        self.metrics = MetricsWidget()
        self.addTab(self.summary, "Summary")
        self.addTab(self.explanation, "Explanation")
        self.addTab(QLabel(), "Input")
        self.addTab(QLabel(), "Output")
        self.addTab(self.metrics, "Metrics")
        self.addTab(QLabel(), "Changes")
        self.addTab(QLabel(), "Evidence")

    def show_run(self, run: RunSummary) -> None:
        self.summary.setText(
            f"{run.trace_id}\nStatus: {run.status}\nRecords: {run.record_count}\n"
            f"Cost: ${run.total_cost:.4f}"
        )
        self.explanation.setText(explain_run(run))
        self.metrics.set_metrics(run.total_usage, run.total_cost)

    def show_agent(self, detail: AgentDetail) -> None:
        duration = (
            f"{detail.duration_seconds:.3f}s"
            if detail.duration_seconds is not None
            else "incomplete"
        )
        self.summary.setText(
            f"Agent: {detail.relationship.child_span_id}\n"
            f"Parent: {detail.relationship.parent_span_id or 'Root'}\n"
            f"Relationship: {detail.relationship.status.value}\n"
            f"Handoff: {json.dumps(detail.handoff, sort_keys=True)}\n"
            f"Returned: {detail.returned_result}\n"
            f"Duration: {duration}\n"
            f"Prompt tokens: {detail.usage.prompt_tokens}\n"
            f"Cost: ${detail.cost:.4f}"
        )
        self.explanation.setText(explain_agent(detail))
        self.metrics.set_metrics(detail.usage, detail.cost)

    def show_finding(self, finding: Finding) -> None:
        self.summary.setText(
            f"{finding.severity.upper()}: {finding.title}\n"
            f"{finding.explanation}\n"
            f"Recommendation: {finding.recommendation}\n"
            f"Evidence: {', '.join(finding.evidence_ids)}"
        )
        self.explanation.setText(explain_finding(finding))

    def show_record(
        self, record: Record, related_user_message: Record | None = None
    ) -> None:
        self.summary.set_styled_summary(
            format_styled_record_summary(record, related_user_message)
        )
        self.explanation.setText(explain_record(record))
