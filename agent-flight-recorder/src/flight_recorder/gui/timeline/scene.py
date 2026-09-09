from collections.abc import Sequence

from flight_recorder.gui.timeline.items import (
    HEADER_HEIGHT,
    LABEL_WIDTH,
    LANE_HEIGHT,
    SEQUENCE_WIDTH,
    TimelineRecordItem,
    TimelineSpanItem,
    record_label,
)
from flight_recorder.gui.timeline.layout import SpanLayout, project_spans
from flight_recorder.gui.timeline.rows import (
    ACTIVITY_ROW_TYPES,
    DEFAULT_ACTIVITY_ROW_TYPES,
    classify_record,
)
from flight_recorder.gui.timeline.time import (
    RecordInterval,
    project_record_intervals,
)
from flight_recorder.models.envelopes import Record, Span
from PySide6.QtCore import QLineF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene
from shiboken6 import isValid


class TimelineScene(QGraphicsScene):
    record_activated = Signal(str)
    span_activated = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._layouts: list[SpanLayout] = []
        self._record_lanes: dict[str, tuple[int, int]] = {}
        self._record_intervals: dict[str, RecordInterval] = {}
        self._record_labels: dict[str, str] = {}
        self._span_lanes: dict[str, int] = {}
        self._floating_labels: list[tuple[QGraphicsItem, float]] = []
        self._spans: tuple[Span, ...] = ()
        self._records: tuple[Record, ...] = ()
        self._current_sequence = 1
        self.lane_names: tuple[str, ...] = ()
        self.axis_labels: tuple[str, ...] = ()
        self.available_row_types: tuple[str, ...] = ()
        self.enabled_row_types: tuple[str, ...] = ()
        self.sequence_width = SEQUENCE_WIDTH
        self.time_zoom = 1.0
        self.pixels_per_second = 1.0
        self.elapsed_seconds = 0.0

    def set_spans(self, spans: Sequence[Span], *, current_sequence: int) -> None:
        self.set_timeline(spans, (), current_sequence=current_sequence)

    def set_timeline(
        self,
        spans: Sequence[Span],
        records: Sequence[Record],
        *,
        current_sequence: int,
        enabled_row_types: set[str] | None = None,
    ) -> None:
        self._spans = tuple(spans)
        self._records = tuple(records)
        self._current_sequence = current_sequence
        self._floating_labels = []
        self.clear()
        self._layouts = project_spans(spans, current_sequence=current_sequence)
        self._record_lanes = {}
        self._record_intervals = {}
        self._record_labels = {}
        self._span_lanes = {}
        time_projection = project_record_intervals(list(records))
        self.elapsed_seconds = time_projection.elapsed_seconds
        self.pixels_per_second = (
            max(
                0.5,
                min(80.0, 1800.0 / max(1.0, self.elapsed_seconds)),
            )
            * self.time_zoom
        )
        spans_by_id = {span.span_id: span for span in spans}
        agent_ids = []
        for layout in self._layouts:
            span = spans_by_id[layout.span_id]
            agent_id = span.agent_span_id or span.span_id
            if agent_id not in agent_ids:
                agent_ids.append(agent_id)
        for record in records:
            if record.agent_span_id and record.agent_span_id not in agent_ids:
                agent_ids.append(record.agent_span_id)
        if not agent_ids:
            agent_ids.append("session")
        default_agent_id = agent_ids[0]
        agent_names = {
            agent_id: (
                spans_by_id[agent_id].name
                if agent_id in spans_by_id
                else "Session"
                if agent_id == "session"
                else agent_id
            )
            for agent_id in agent_ids
        }
        category_by_record = {
            record.record_id: classify_record(record) for record in records
        }
        present_by_agent: dict[str, set[str]] = {
            agent_id: set() for agent_id in agent_ids
        }
        for record in records:
            agent_id = record.agent_span_id or default_agent_id
            present_by_agent.setdefault(agent_id, set()).add(
                category_by_record[record.record_id]
            )
        for layout in self._layouts:
            span = spans_by_id[layout.span_id]
            present_by_agent[span.agent_span_id or span.span_id].add("lifecycle")
        present_types = {
            row_type
            for row_types in present_by_agent.values()
            for row_type in row_types
        }
        self.available_row_types = tuple(
            row.key for row in ACTIVITY_ROW_TYPES if row.key in present_types
        )
        enabled = (
            set(self.available_row_types) & DEFAULT_ACTIVITY_ROW_TYPES
            if enabled_row_types is None
            else set(enabled_row_types) & present_types
        )
        if not enabled and self.available_row_types:
            enabled.add(self.available_row_types[0])
        self.enabled_row_types = tuple(
            row.key for row in ACTIVITY_ROW_TYPES if row.key in enabled
        )
        rows = []
        row_by_agent_type: dict[tuple[str, str], int] = {}
        for agent_id in agent_ids:
            for row_type in ACTIVITY_ROW_TYPES:
                if row_type.key in enabled and row_type.key in present_by_agent.get(
                    agent_id, set()
                ):
                    row_by_agent_type[(agent_id, row_type.key)] = len(rows)
                    rows.append(
                        (
                            agent_id,
                            agent_names[agent_id],
                            row_type.label,
                            row_type.color,
                            row_type.description,
                        )
                    )
        self.lane_names = tuple(
            f"{row_label} · {agent_name}" for _, agent_name, row_label, _, _ in rows
        )

        scene_width = (
            LABEL_WIDTH + max(1.0, self.elapsed_seconds) * self.pixels_per_second + 24
        )
        scene_height = HEADER_HEIGHT + max(1, len(rows)) * LANE_HEIGHT
        self.setBackgroundBrush(QBrush(QColor("#f4f6f5")))
        header = self.addRect(
            QRectF(0, 0, scene_width, HEADER_HEIGHT),
            QPen(Qt.PenStyle.NoPen),
            QBrush(QColor("#e8ecea")),
        )
        header.setZValue(-1)
        label_header_background = self.addRect(
            QRectF(0, 0, LABEL_WIDTH, HEADER_HEIGHT),
            QPen(QColor("#bdc7c2"), 1),
            QBrush(QColor("#e8ecea")),
        )
        label_header_background.setZValue(3)
        self._floating_labels.append((label_header_background, 0.0))
        label_header = self.addText("Agent / activity rows")
        label_header.setPos(10, 9)
        label_header.setDefaultTextColor(QColor("#26332e"))
        label_header.setFont(QFont("", 9, QFont.Weight.DemiBold))
        label_header.setZValue(4)
        self._floating_labels.append((label_header, 10.0))
        tick_step = self._time_tick_step(self.elapsed_seconds)
        axis_labels = []
        tick = 0.0
        while tick <= self.elapsed_seconds:
            x = LABEL_WIDTH + tick * self.pixels_per_second
            self.addLine(
                QLineF(x, HEADER_HEIGHT - 7, x, scene_height),
                QPen(QColor("#d7ddda"), 1, Qt.PenStyle.DotLine),
            ).setZValue(-1)
            label = self._time_label(tick)
            axis_labels.append(label)
            text = self.addText(label)
            text.setPos(x + 3, 7)
            text.setDefaultTextColor(QColor("#52615b"))
            tick += tick_step
        self.axis_labels = tuple(axis_labels)

        for lane, (
            _,
            agent_name,
            row_label,
            row_color,
            row_description,
        ) in enumerate(rows):
            y = HEADER_HEIGHT + lane * LANE_HEIGHT
            fill = QColor("#ffffff") if lane % 2 == 0 else QColor("#eef2f0")
            self.addRect(
                QRectF(0, y, scene_width, LANE_HEIGHT),
                QPen(QColor("#d4dad7"), 1),
                QBrush(fill),
            ).setZValue(-2)
            label_background = self.addRect(
                QRectF(0, y, LABEL_WIDTH, LANE_HEIGHT),
                QPen(QColor("#d4dad7"), 1),
                QBrush(fill),
            )
            label_background.setZValue(3)
            row_tooltip = f"{row_label} · {agent_name}\n{row_description}"
            label_background.setToolTip(row_tooltip)
            self._floating_labels.append((label_background, 0.0))
            row_marker = self.addRect(
                QRectF(6, y + 16, 4, 24),
                QPen(Qt.PenStyle.NoPen),
                QBrush(QColor(row_color)),
            )
            row_marker.setZValue(4)
            row_marker.setToolTip(row_tooltip)
            self._floating_labels.append((row_marker, 0.0))
            display_agent = (
                agent_name if len(agent_name) <= 27 else agent_name[:24] + "..."
            )
            display_name = f"{row_label}\n{display_agent}"
            text = self.addText(display_name)
            text.setPos(16, y + 10)
            text.setDefaultTextColor(QColor("#26332e"))
            text.setToolTip(row_tooltip)
            text.setZValue(4)
            self._floating_labels.append((text, 16.0))

        for layout in self._layouts:
            span = spans_by_id[layout.span_id]
            row = row_by_agent_type.get(
                (span.agent_span_id or span.span_id, "lifecycle")
            )
            if row is None:
                continue
            self._span_lanes[layout.span_id] = row
            span_records = [
                record
                for record in records
                if record.span_id == span.span_id
                or (
                    span.span_type == "agent"
                    and record.agent_span_id == span.agent_span_id
                )
            ]
            span_intervals = [
                time_projection.intervals[record.record_id]
                for record in span_records
                if record.record_id in time_projection.intervals
            ]
            start_seconds = (
                min(interval.start_seconds for interval in span_intervals)
                if span_intervals
                else 0.0
            )
            end_seconds = (
                max(interval.end_seconds for interval in span_intervals)
                if span_intervals
                else self.elapsed_seconds
            )
            self.addItem(
                TimelineSpanItem(
                    layout,
                    start_seconds,
                    end_seconds,
                    self.span_activated.emit,
                    self.pixels_per_second,
                    row,
                )
            )
        for record in records:
            if record.sequence is None:
                continue
            agent_id = record.agent_span_id or default_agent_id
            lane = row_by_agent_type.get(
                (agent_id, category_by_record[record.record_id])
            )
            if lane is None:
                continue
            self._record_lanes[record.record_id] = (record.sequence, lane)
            self._record_labels[record.record_id] = record_label(record)
            interval = time_projection.intervals[record.record_id]
            self._record_intervals[record.record_id] = interval
            self.addItem(
                TimelineRecordItem(
                    record,
                    interval,
                    lane=lane,
                    on_activated=self.record_activated.emit,
                    pixels_per_second=self.pixels_per_second,
                )
            )
        self.setSceneRect(QRectF(0, 0, scene_width, scene_height))

    def set_label_offset(self, offset: float) -> None:
        self._floating_labels = [
            (item, base_x) for item, base_x in self._floating_labels if isValid(item)
        ]
        for item, base_x in self._floating_labels:
            item.setX(offset + base_x)

    def set_enabled_row_types(self, enabled_row_types: set[str]) -> None:
        self.set_timeline(
            self._spans,
            self._records,
            current_sequence=self._current_sequence,
            enabled_row_types=enabled_row_types,
        )

    def set_time_zoom(self, zoom: float) -> None:
        self.time_zoom = zoom
        self.set_timeline(
            self._spans,
            self._records,
            current_sequence=self._current_sequence,
            enabled_row_types=set(self.enabled_row_types),
        )

    @staticmethod
    def _time_tick_step(elapsed_seconds: float) -> float:
        if elapsed_seconds <= 10:
            return 1
        if elapsed_seconds <= 60:
            return 5
        if elapsed_seconds <= 300:
            return 30
        if elapsed_seconds <= 1800:
            return 120
        return 300

    @staticmethod
    def _time_label(seconds: float) -> str:
        if seconds < 60:
            return f"+{seconds:g}s"
        minutes, remainder = divmod(int(seconds), 60)
        return f"+{minutes}m{remainder:02d}s"

    def span_at(self, *, sequence: int, lane: int) -> str | None:
        for layout in self._layouts:
            if (
                self._span_lanes.get(layout.span_id) == lane
                and layout.start <= sequence <= layout.end
            ):
                return layout.span_id
        return None

    def record_at(self, *, sequence: int, lane: int) -> str | None:
        return next(
            (
                record_id
                for record_id, position in self._record_lanes.items()
                if position == (sequence, lane)
            ),
            None,
        )

    def record_label(self, record_id: str) -> str:
        return self._record_labels[record_id]

    def record_interval(self, record_id: str) -> RecordInterval:
        return self._record_intervals[record_id]

    @property
    def lane_height(self) -> float:
        return LANE_HEIGHT
