"""Main trace investigation window."""

from collections.abc import Callable
from pathlib import Path

from flight_recorder.gui.controllers.selection import SelectionController
from flight_recorder.gui.inspector.widget import InspectorWidget
from flight_recorder.gui.models.findings import FindingsModel
from flight_recorder.gui.models.run_table import RunTableModel
from flight_recorder.gui.models.trace_tree import TraceTreeModel
from flight_recorder.gui.timeline.rows import (
    ACTIVITY_ROW_TYPES,
    DEFAULT_ACTIVITY_ROW_TYPES,
)
from flight_recorder.gui.timeline.view import TimelineViewWidget
from flight_recorder.models.envelopes import Finding
from flight_recorder.models.view_models import RunQuery
from flight_recorder.services.repository import TraceRepository
from PySide6.QtCore import QByteArray, QModelIndex, QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QSplitter,
    QTableView,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


DEFAULT_WINDOW_WIDTH = 1200
DEFAULT_WINDOW_HEIGHT = 760


class MainWindow(QMainWindow):
    def __init__(
        self,
        trace: Path | None = None,
        *,
        repository: TraceRepository | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__()
        self._detach_callbacks: list[Callable[[], None]] = []
        self._detached = False
        self._settings: QSettings | None = None
        self._repository: TraceRepository | None = None
        self._trace_id: str | None = None
        self._hidden_timeline_row_types = {
            row.key
            for row in ACTIVITY_ROW_TYPES
            if row.key not in DEFAULT_ACTIVITY_ROW_TYPES
        }
        self._updating_timeline_row_actions = False
        self.selection = SelectionController()
        self.setWindowTitle("Agent Flight Recorder")
        self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("workspace-splitter")
        self.splitter.setHandleWidth(8)
        self.splitter.setOpaqueResize(True)
        self.splitter.setStyleSheet(
            "QSplitter::handle:horizontal {"
            "background: #c7ceca; border-left: 1px solid #9ba7a1; "
            "border-right: 1px solid #9ba7a1; margin: 2px 1px;"
            "}"
            "QSplitter::handle:horizontal:hover, "
            "QSplitter::handle:horizontal:pressed { background: #5d8b78; }"
        )
        navigation = QWidget()
        navigation.setMinimumWidth(160)
        navigation_layout = QVBoxLayout(navigation)
        run_table = QTableView()
        run_table.setObjectName("runs")
        self.run_model = RunTableModel(parent=run_table)
        run_table.setModel(self.run_model)
        self.tree = QTreeView()
        self.tree.setObjectName("trace-tree")
        self.tree_model = TraceTreeModel(parent=self.tree)
        self.tree.setModel(self.tree_model)
        self.tree.clicked.connect(self._tree_item_selected)
        navigation_layout.addWidget(run_table)
        navigation_layout.addWidget(self.tree)
        self.findings = QTableView()
        self.findings.setObjectName("findings")
        self.findings_model = FindingsModel(parent=self.findings)
        self.findings.setModel(self.findings_model)
        self.findings.doubleClicked.connect(self._finding_selected)
        navigation_layout.addWidget(self.findings)

        center = QWidget()
        center.setMinimumWidth(240)
        center_layout = QVBoxLayout(center)
        timeline_controls = QHBoxLayout()
        timeline_controls.addStretch()
        self.timeline = TimelineViewWidget()
        self.timeline_zoom_out = QToolButton()
        self.timeline_zoom_out.setObjectName("timeline-zoom-out")
        self.timeline_zoom_out.setText("−")
        self.timeline_zoom_out.setToolTip("Zoom out (mouse wheel down)")
        self.timeline_zoom_out.clicked.connect(self.timeline.zoom_out)
        self.timeline_fit = QToolButton()
        self.timeline_fit.setObjectName("timeline-zoom-fit")
        self.timeline_fit.setText("Fit")
        self.timeline_fit.setToolTip("Fit elapsed timeline (0)")
        self.timeline_zoom_value = QLabel("100%")
        self.timeline_zoom_value.setMinimumWidth(44)
        self.timeline_zoom_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timeline_zoom_in = QToolButton()
        self.timeline_zoom_in.setObjectName("timeline-zoom-in")
        self.timeline_zoom_in.setText("+")
        self.timeline_zoom_in.setToolTip("Zoom in (mouse wheel up)")
        self.timeline_fit.clicked.connect(self.timeline.zoom_to_fit)
        self.timeline_zoom_in.clicked.connect(self.timeline.zoom_in)
        self.timeline.zoom_changed.connect(
            lambda zoom: self.timeline_zoom_value.setText(f"{zoom * 100:.0f}%")
        )
        self.timeline_rows_button = QToolButton()
        self.timeline_rows_button.setObjectName("timeline-row-types")
        self.timeline_rows_button.setText("Rows")
        self.timeline_rows_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        row_menu = QMenu(self.timeline_rows_button)
        self.timeline_row_actions: dict[str, QAction] = {}
        for row_type in ACTIVITY_ROW_TYPES:
            action = row_menu.addAction(row_type.label)
            action.setCheckable(True)
            action.toggled.connect(
                lambda checked, key=row_type.key: self._timeline_row_type_toggled(
                    key, checked
                )
            )
            self.timeline_row_actions[row_type.key] = action
        self.timeline_rows_button.setMenu(row_menu)
        timeline_controls.addWidget(self.timeline_rows_button)
        timeline_controls.addWidget(self.timeline_zoom_out)
        timeline_controls.addWidget(self.timeline_fit)
        timeline_controls.addWidget(self.timeline_zoom_in)
        timeline_controls.addWidget(self.timeline_zoom_value)
        center_layout.addLayout(timeline_controls)
        self.timeline.span_activated.connect(self.navigate_to_agent)
        self.timeline.record_activated.connect(self.navigate_to_record)
        changes = QLabel("Workspace changes")
        changes.setObjectName("workspace-changes")
        center_layout.addWidget(self.timeline)
        center_layout.addWidget(changes)

        self.inspector = InspectorWidget()
        self.inspector.setMinimumWidth(160)
        if trace is not None:
            self.inspector.summary.setText(str(trace))
        self.splitter.addWidget(navigation)
        self.splitter.addWidget(center)
        self.splitter.addWidget(self.inspector)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([250, 700, 250])
        self.setCentralWidget(self.splitter)
        if repository is not None:
            self.set_repository(repository, trace_id)

    def set_repository(
        self, repository: TraceRepository, trace_id: str | None = None
    ) -> None:
        self._repository = repository
        runs = repository.list_runs(RunQuery())
        self.run_model.set_runs(runs)
        selected_trace_id = trace_id or (runs[0].trace_id if runs else None)
        if selected_trace_id is None:
            return
        self._trace_id = selected_trace_id
        run = repository.get_run(selected_trace_id)
        timeline = repository.get_timeline(selected_trace_id)
        self.tree_model.set_timeline(timeline)
        current_sequence = timeline.records[-1].sequence or 1
        self.timeline.timeline_scene.set_timeline(
            timeline.spans,
            timeline.records,
            current_sequence=current_sequence,
            enabled_row_types={
                row.key
                for row in ACTIVITY_ROW_TYPES
                if row.key not in self._hidden_timeline_row_types
            },
        )
        self._sync_timeline_row_actions()
        self.inspector.show_run(run)

    def _sync_timeline_row_actions(self) -> None:
        available = set(self.timeline.timeline_scene.available_row_types)
        enabled = set(self.timeline.timeline_scene.enabled_row_types)
        self._updating_timeline_row_actions = True
        try:
            for key, action in self.timeline_row_actions.items():
                action.setEnabled(key in available)
                action.setChecked(key in enabled)
        finally:
            self._updating_timeline_row_actions = False

    def _timeline_row_type_toggled(self, key: str, checked: bool) -> None:
        if self._updating_timeline_row_actions:
            return
        if checked:
            self._hidden_timeline_row_types.discard(key)
        else:
            self._hidden_timeline_row_types.add(key)
        available = set(self.timeline.timeline_scene.available_row_types)
        enabled = available - self._hidden_timeline_row_types
        if not enabled:
            self._hidden_timeline_row_types.discard(key)
            self._sync_timeline_row_actions()
            return
        self.timeline.timeline_scene.set_enabled_row_types(enabled)
        self._sync_timeline_row_actions()

    def navigate_to_agent(self, span_id: str) -> None:
        self.selection.select_span(span_id)
        index = self.tree_model.index_for_id(span_id)
        if index.isValid():
            parent = index.parent()
            while parent.isValid():
                self.tree.expand(parent)
                parent = parent.parent()
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)
        if self._repository is not None and self._trace_id is not None:
            self.inspector.show_agent(
                self._repository.get_agent_detail(self._trace_id, span_id)
            )

    def set_findings(self, findings: list[Finding]) -> None:
        self.findings_model.set_findings(findings)

    def navigate_to_finding(self, row: int) -> None:
        finding = self.findings_model.finding_at(row)
        self.findings.setCurrentIndex(self.findings_model.index(row, 0))
        self.inspector.show_finding(finding)
        self.selection.navigate_to_evidence(finding.evidence_ids[0])

    def navigate_to_record(self, record_id: str) -> None:
        self.selection.select_record(record_id)
        if self._repository is not None and self._trace_id is not None:
            record = self._repository.get_record(self._trace_id, record_id)
            related_user_message = None
            if (
                record.sequence is not None
                and record.payload.get("event_type") == "SystemPromptEvent"
            ):
                related_user_message = self._repository.get_next_user_message(
                    self._trace_id,
                    after_sequence=record.sequence,
                    agent_span_id=record.agent_span_id,
                )
            self.inspector.show_record(
                record, related_user_message=related_user_message
            )

    def _finding_selected(self, index: QModelIndex) -> None:
        self.navigate_to_finding(index.row())

    def _tree_item_selected(self, index: QModelIndex) -> None:
        item_id = index.data(Qt.ItemDataRole.UserRole + 1)
        if isinstance(item_id, str):
            span_ids = {span.span_id for span in self.tree_model_timeline_spans()}
            if item_id in span_ids:
                self.navigate_to_agent(item_id)
            else:
                self.navigate_to_record(item_id)

    def tree_model_timeline_spans(self) -> tuple:
        if self._repository is None or self._trace_id is None:
            return ()
        return self._repository.get_timeline(self._trace_id).spans

    def save_settings(self, settings: QSettings) -> None:
        settings.setValue("window/width", self.width())
        settings.setValue("window/height", self.height())
        settings.setValue("window/splitter", self.splitter.saveState())
        settings.setValue(
            "timeline/hidden-row-types",
            sorted(self._hidden_timeline_row_types),
        )
        settings.sync()

    def restore_settings(self, settings: QSettings) -> None:
        self._settings = settings
        width = settings.value("window/width")
        height = settings.value("window/height")
        if width is not None and height is not None:
            screen = QApplication.primaryScreen()
            available = screen.availableGeometry() if screen is not None else None
            maximum_width = max(
                DEFAULT_WINDOW_WIDTH,
                available.width() if available is not None else 0,
            )
            maximum_height = max(
                DEFAULT_WINDOW_HEIGHT,
                available.height() if available is not None else 0,
            )
            self.resize(
                min(max(int(width), self.minimumSizeHint().width()), maximum_width),
                min(max(int(height), self.minimumSizeHint().height()), maximum_height),
            )
        splitter_state = settings.value("window/splitter")
        if isinstance(splitter_state, QByteArray):
            self.splitter.restoreState(splitter_state)
        if settings.contains("timeline/hidden-row-types"):
            stored_row_types = settings.value("timeline/hidden-row-types", [])
            if isinstance(stored_row_types, str):
                hidden_row_types = [stored_row_types]
            elif isinstance(stored_row_types, list):
                hidden_row_types = [str(key) for key in stored_row_types]
            else:
                hidden_row_types = []
            known_row_types = {row.key for row in ACTIVITY_ROW_TYPES}
            self._hidden_timeline_row_types = {
                str(key) for key in hidden_row_types if str(key) in known_row_types
            }
        if self._repository is not None:
            self.timeline.timeline_scene.set_enabled_row_types(
                set(self.timeline.timeline_scene.available_row_types)
                - self._hidden_timeline_row_types
            )
            self._sync_timeline_row_actions()

    def add_detach_callback(self, callback: Callable[[], None]) -> None:
        self._detach_callbacks.append(callback)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._detached:
            self._detached = True
            for callback in self._detach_callbacks:
                callback()
            if self._settings is not None:
                self.save_settings(self._settings)
        super().closeEvent(event)

    def centralWidget(self) -> QWidget:  # noqa: N802
        widget = super().centralWidget()
        assert widget is not None
        return widget
