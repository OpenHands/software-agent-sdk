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
    format_llm_input,
    format_llm_output,
    format_readable_data,
    format_styled_record_summary,
)
from flight_recorder.models.envelopes import Finding, Record
from flight_recorder.models.view_models import AgentDetail, RunSummary
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QKeyEvent,
    QKeySequence,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
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


class InspectorFindEdit(QLineEdit):
    navigate_requested = Signal(bool)
    dismiss_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            backward = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.navigate_requested.emit(backward)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class InspectorWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inspector")
        self.setAccessibleName("Activity inspector")
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.find_bar = QWidget()
        self.find_bar.setObjectName("inspector-find-bar")
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(4, 4, 4, 0)
        find_layout.setSpacing(2)
        self.find_input = InspectorFindEdit()
        self.find_input.setObjectName("inspector-find-input")
        self.find_input.setPlaceholderText("Find")
        self.find_input.setClearButtonEnabled(True)
        self.find_status = QLabel()
        self.find_status.setObjectName("inspector-find-status")
        self.find_status.setMinimumWidth(34)
        self.find_previous = QToolButton()
        self.find_previous.setObjectName("inspector-find-previous")
        self.find_previous.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
        )
        self.find_previous.setToolTip("Previous match")
        self.find_next = QToolButton()
        self.find_next.setObjectName("inspector-find-next")
        self.find_next.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.find_next.setToolTip("Next match")
        self.find_close = QToolButton()
        self.find_close.setObjectName("inspector-find-close")
        self.find_close.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        self.find_close.setToolTip("Close find")
        find_layout.addWidget(self.find_input, 1)
        find_layout.addWidget(self.find_status)
        find_layout.addWidget(self.find_previous)
        find_layout.addWidget(self.find_next)
        find_layout.addWidget(self.find_close)
        layout.addWidget(self.find_bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.summary = InspectorTextView("No selection")
        self.explanation = InspectorTextView(
            "Select a run, agent, finding, or activity to see its explanation."
        )
        self.input = InspectorTextView("No LLM request selected.")
        self.output = InspectorTextView("No LLM response selected.")
        self.metrics = MetricsWidget()
        self.addTab(self.summary, "Summary")
        self.addTab(self.explanation, "Explanation")
        self.addTab(self.input, "Input")
        self.addTab(self.output, "Output")
        self.addTab(self.metrics, "Metrics")
        self.addTab(QLabel(), "Changes")
        self.addTab(QLabel(), "Evidence")
        self.find_bar.hide()

        self.find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.find_shortcut.activated.connect(self.show_find)
        self.find_input.textChanged.connect(self._find_text_changed)
        self.find_input.navigate_requested.connect(self._find_match)
        self.find_input.dismiss_requested.connect(self.hide_find)
        self.find_previous.clicked.connect(lambda: self._find_match(True))
        self.find_next.clicked.connect(lambda: self._find_match(False))
        self.find_close.clicked.connect(self.hide_find)
        self.tabs.currentChanged.connect(self._active_tab_changed)
        for view in (self.summary, self.explanation, self.input, self.output):
            view.textChanged.connect(self._active_document_changed)

    def addTab(self, widget: QWidget, label: str) -> int:  # noqa: N802
        return self.tabs.addTab(widget, label)

    def count(self) -> int:
        return self.tabs.count()

    def indexOf(self, widget: QWidget) -> int:  # noqa: N802
        return self.tabs.indexOf(widget)

    def tabText(self, index: int) -> str:  # noqa: N802
        return self.tabs.tabText(index)

    def currentWidget(self) -> QWidget | None:  # noqa: N802
        return self.tabs.currentWidget()

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self.tabs.setCurrentIndex(index)

    def setCurrentWidget(self, widget: QWidget) -> None:  # noqa: N802
        self.tabs.setCurrentWidget(widget)

    def show_find(self) -> None:
        self.find_bar.show()
        self.find_input.setFocus()
        self.find_input.selectAll()
        self._find_match(False, reset=True)

    def hide_find(self) -> None:
        self.find_bar.hide()
        current = self.currentWidget()
        if current is not None:
            current.setFocus()

    def _active_text_view(self) -> InspectorTextView | None:
        current = self.currentWidget()
        return current if isinstance(current, InspectorTextView) else None

    def _find_text_changed(self) -> None:
        self._find_match(False, reset=True)

    def _active_tab_changed(self) -> None:
        if self.find_bar.isVisible():
            self._find_match(False, reset=True)

    def _active_document_changed(self) -> None:
        if self.find_bar.isVisible() and self.sender() is self.currentWidget():
            self._find_match(False, reset=True)

    def _find_match(self, backward: bool, *, reset: bool = False) -> None:
        view = self._active_text_view()
        query = self.find_input.text()
        searchable = view is not None and bool(query)
        self.find_previous.setEnabled(searchable)
        self.find_next.setEnabled(searchable)
        if view is None:
            self.find_status.setText("N/A")
            return
        if not query:
            self.find_status.clear()
            return

        matches = self._find_ranges(view.document(), query)
        if not matches:
            self.find_status.setText("0/0")
            cursor = view.textCursor()
            cursor.clearSelection()
            view.setTextCursor(cursor)
            return

        current_start = view.textCursor().selectionStart()
        if reset:
            match_index = len(matches) - 1 if backward else 0
        elif backward:
            match_index = next(
                (
                    index
                    for index in range(len(matches) - 1, -1, -1)
                    if matches[index][0] < current_start
                ),
                len(matches) - 1,
            )
        else:
            match_index = next(
                (
                    index
                    for index, match in enumerate(matches)
                    if match[0] > current_start
                ),
                0,
            )
        start, end = matches[match_index]
        cursor = QTextCursor(view.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        view.setTextCursor(cursor)
        view.ensureCursorVisible()
        self.find_status.setText(f"{match_index + 1}/{len(matches)}")

    @staticmethod
    def _find_ranges(document: QTextDocument, query: str) -> list[tuple[int, int]]:
        matches = []
        cursor = QTextCursor(document)
        while True:
            cursor = document.find(query, cursor)
            if cursor.isNull():
                return matches
            matches.append((cursor.selectionStart(), cursor.selectionEnd()))

    def show_run(self, run: RunSummary) -> None:
        self._clear_exchange()
        self.summary.setText(
            f"{run.trace_id}\nStatus: {run.status}\nRecords: {run.record_count}\n"
            f"Cost: ${run.total_cost:.4f}"
        )
        self.explanation.setText(explain_run(run))
        self.metrics.set_metrics(run.total_usage, run.total_cost)

    def show_agent(self, detail: AgentDetail) -> None:
        self._clear_exchange()
        duration = (
            f"{detail.duration_seconds:.3f}s"
            if detail.duration_seconds is not None
            else "incomplete"
        )
        self.summary.setText(
            f"Agent: {detail.relationship.child_span_id}\n"
            f"Parent: {detail.relationship.parent_span_id or 'Root'}\n"
            f"Relationship: {detail.relationship.status.value}\n"
            f"Handoff:\n{format_readable_data(detail.handoff)}\n"
            f"Returned:\n{format_readable_data(detail.returned_result)}\n"
            f"Duration: {duration}\n"
            f"Prompt tokens: {detail.usage.prompt_tokens}\n"
            f"Cost: ${detail.cost:.4f}"
        )
        self.explanation.setText(explain_agent(detail))
        self.metrics.set_metrics(detail.usage, detail.cost)

    def show_finding(self, finding: Finding) -> None:
        self._clear_exchange()
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
        self.input.setText(format_llm_input(record))
        self.output.setText(format_llm_output(record))

    def _clear_exchange(self) -> None:
        self.input.setText("No LLM request selected.")
        self.output.setText("No LLM response selected.")
