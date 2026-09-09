from collections.abc import Sequence

from flight_recorder.gui.timeline.items import (
    HEADER_HEIGHT,
    LABEL_WIDTH,
    LANE_HEIGHT,
    RECORD_HEIGHT,
    RECORD_TOP,
    SEQUENCE_WIDTH,
    TimelineRecordItem,
    TimelineSequenceArrowItem,
    TimelineSpanItem,
    record_label,
    record_width,
)
from flight_recorder.gui.timeline.layout import SpanLayout, project_spans
from flight_recorder.gui.timeline.rows import (
    ACTIVITY_ROW_TYPES,
    DEFAULT_ACTIVITY_ROW_TYPES,
    ActivityRowType,
    classify_record,
    row_key_for_category,
)
from flight_recorder.gui.timeline.time import (
    RecordInterval,
    project_record_intervals,
)
from flight_recorder.models.envelopes import Record, Span
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene
from shiboken6 import isValid


_RECORD_SEQUENCE_GAP = 6.0
_GROUP_HEADER_HEIGHT = 28.0
_SUBROW_HEIGHT = 32.0


def _row_height(row_type: ActivityRowType) -> float:
    if not row_type.subrows:
        return LANE_HEIGHT
    return _GROUP_HEADER_HEIGHT + len(row_type.subrows) * _SUBROW_HEIGHT


def _record_display_y(
    row_top: float,
    row_type: ActivityRowType,
    category: str,
) -> float:
    if not row_type.subrows:
        return row_top + RECORD_TOP
    subrow_index = next(
        index for index, subrow in enumerate(row_type.subrows) if subrow.key == category
    )
    return (
        row_top
        + _GROUP_HEADER_HEIGHT
        + subrow_index * _SUBROW_HEIGHT
        + (_SUBROW_HEIGHT - RECORD_HEIGHT) / 2
    )


def _project_record_layouts(
    records: Sequence[Record],
    row_by_record: dict[str, int],
    intervals: dict[str, RecordInterval],
    pixels_per_second: float,
) -> tuple[dict[str, float], list[tuple[str, str]]]:
    indexed_records = [
        (source_index, record)
        for source_index, record in enumerate(records)
        if record.record_id in row_by_record and record.sequence is not None
    ]
    ordered_records = [
        record
        for _, record in sorted(
            indexed_records,
            key=lambda item: (
                item[1].sequence or 0,
                item[0],
            ),
        )
    ]
    display_start_by_record = {}
    transitions = []
    previous_record = None
    for record in ordered_records:
        interval = intervals[record.record_id]
        display_start = interval.start_seconds * pixels_per_second
        if previous_record is not None:
            previous_start = display_start_by_record[previous_record.record_id]
            previous_interval = intervals[previous_record.record_id]
            previous_end = previous_start + record_width(
                previous_interval, pixels_per_second
            )
            display_start = max(
                display_start,
                previous_end + _RECORD_SEQUENCE_GAP,
            )
            transitions.append((previous_record.record_id, record.record_id))
        display_start_by_record[record.record_id] = display_start
        previous_record = record
    return display_start_by_record, transitions


class TimelineScene(QGraphicsScene):
    record_activated = Signal(str)
    span_activated = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._layouts: list[SpanLayout] = []
        self._record_lanes: dict[str, tuple[int, int]] = {}
        self._record_intervals: dict[str, RecordInterval] = {}
        self._record_labels: dict[str, str] = {}
        self._record_items: dict[str, TimelineRecordItem] = {}
        self._selected_record_id: str | None = None
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
        self._record_items = {}
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
                row_key_for_category(category_by_record[record.record_id])
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
                    rows.append((agent_id, agent_names[agent_id], row_type))
        self.lane_names = tuple(
            f"{row_type.label} · {agent_name}" for _, agent_name, row_type in rows
        )

        row_by_record = {}
        for record in records:
            agent_id = record.agent_span_id or default_agent_id
            row = row_by_agent_type.get(
                (
                    agent_id,
                    row_key_for_category(category_by_record[record.record_id]),
                )
            )
            if row is not None:
                row_by_record[record.record_id] = row
        display_start_by_record, sequence_transitions = _project_record_layouts(
            records,
            row_by_record,
            time_projection.intervals,
            self.pixels_per_second,
        )
        row_heights = [_row_height(row_type) for _, _, row_type in rows]
        row_tops = []
        next_row_top = HEADER_HEIGHT
        for row_height in row_heights:
            row_tops.append(next_row_top)
            next_row_top += row_height

        drawn_record_end = max(
            (
                display_start_by_record[record.record_id]
                + record_width(
                    time_projection.intervals[record.record_id],
                    self.pixels_per_second,
                )
                for record in records
                if record.record_id in display_start_by_record
            ),
            default=0.0,
        )
        timeline_width = max(
            max(1.0, self.elapsed_seconds) * self.pixels_per_second,
            drawn_record_end,
        )
        scene_width = LABEL_WIDTH + timeline_width + 24
        scene_height = max(HEADER_HEIGHT + LANE_HEIGHT, next_row_top)
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

        for lane, (_, agent_name, row_type) in enumerate(rows):
            y = row_tops[lane]
            row_height = row_heights[lane]
            fill = QColor("#ffffff") if lane % 2 == 0 else QColor("#eef2f0")
            self.addRect(
                QRectF(0, y, scene_width, row_height),
                QPen(QColor("#d4dad7"), 1),
                QBrush(fill),
            ).setZValue(-2)
            label_background = self.addRect(
                QRectF(0, y, LABEL_WIDTH, row_height),
                QPen(QColor("#d4dad7"), 1),
                QBrush(fill),
            )
            label_background.setZValue(3)
            row_tooltip = f"{row_type.label} · {agent_name}\n{row_type.description}"
            label_background.setToolTip(row_tooltip)
            self._floating_labels.append((label_background, 0.0))
            row_marker = self.addRect(
                QRectF(6, y + 16, 4, 24),
                QPen(Qt.PenStyle.NoPen),
                QBrush(QColor(row_type.color)),
            )
            row_marker.setZValue(4)
            row_marker.setToolTip(row_tooltip)
            self._floating_labels.append((row_marker, 0.0))
            display_agent = (
                agent_name if len(agent_name) <= 27 else agent_name[:24] + "..."
            )
            if row_type.subrows:
                title = self.addText(f"{row_type.label} · {display_agent}")
                title.setPos(16, y + 2)
                title.setDefaultTextColor(QColor("#26332e"))
                title.setToolTip(row_tooltip)
                title.setZValue(4)
                self._floating_labels.append((title, 16.0))
                for subrow_index, subrow in enumerate(row_type.subrows):
                    subrow_top = (
                        y + _GROUP_HEADER_HEIGHT + subrow_index * _SUBROW_HEIGHT
                    )
                    self.addLine(
                        QLineF(0, subrow_top, scene_width, subrow_top),
                        QPen(QColor("#d4dad7"), 1),
                    ).setZValue(-1)
                    subrow_tooltip = (
                        f"{row_type.label} / {subrow.label} · {agent_name}\n"
                        f"{subrow.description}"
                    )
                    subrow_marker = self.addRect(
                        QRectF(16, subrow_top + 9, 3, 14),
                        QPen(Qt.PenStyle.NoPen),
                        QBrush(QColor(subrow.color)),
                    )
                    subrow_marker.setToolTip(subrow_tooltip)
                    subrow_marker.setZValue(4)
                    self._floating_labels.append((subrow_marker, 0.0))
                    subrow_text = self.addText(subrow.label)
                    subrow_text.setPos(24, subrow_top + 4)
                    subrow_text.setDefaultTextColor(QColor("#52615b"))
                    subrow_text.setToolTip(subrow_tooltip)
                    subrow_text.setZValue(4)
                    self._floating_labels.append((subrow_text, 24.0))
            else:
                text = self.addText(f"{row_type.label}\n{display_agent}")
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
            display_bounds = [
                (
                    display_start_by_record[record.record_id],
                    display_start_by_record[record.record_id]
                    + record_width(
                        time_projection.intervals[record.record_id],
                        self.pixels_per_second,
                    ),
                )
                for record in span_records
                if record.record_id in display_start_by_record
            ]
            if display_bounds:
                start_seconds = (
                    min(start for start, _ in display_bounds) / self.pixels_per_second
                )
                end_seconds = (
                    max(end for _, end in display_bounds) / self.pixels_per_second
                )
            else:
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
                    row_tops[row],
                )
            )
        record_items = {}
        for record in records:
            if record.sequence is None:
                continue
            lane = row_by_record.get(record.record_id)
            if lane is None:
                continue
            self._record_lanes[record.record_id] = (record.sequence, lane)
            self._record_labels[record.record_id] = record_label(record)
            interval = time_projection.intervals[record.record_id]
            self._record_intervals[record.record_id] = interval
            item = TimelineRecordItem(
                record,
                interval,
                lane=lane,
                on_activated=self.record_activated.emit,
                pixels_per_second=self.pixels_per_second,
                row_top=row_tops[lane],
                display_start_pixels=display_start_by_record[record.record_id],
                display_y=_record_display_y(
                    row_tops[lane],
                    rows[lane][2],
                    category_by_record[record.record_id],
                ),
            )
            record_items[record.record_id] = item
            item.set_selected(record.record_id == self._selected_record_id)
            self.addItem(item)
        self._record_items = record_items
        records_by_id = {record.record_id: record for record in records}
        for previous_id, next_id in sequence_transitions:
            previous_item = record_items[previous_id]
            next_item = record_items[next_id]
            previous_rect = previous_item.rect()
            next_rect = next_item.rect()
            arrow = TimelineSequenceArrowItem(
                previous_id,
                next_id,
                QPointF(previous_rect.right(), previous_rect.center().y()),
                QPointF(next_rect.left(), next_rect.center().y()),
            )
            previous_sequence = records_by_id[previous_id].sequence
            next_sequence = records_by_id[next_id].sequence
            arrow.setToolTip(f"Sequence {previous_sequence} to {next_sequence}")
            self.addItem(arrow)
        self.setSceneRect(QRectF(0, 0, scene_width, scene_height))

    def set_selected_record(self, record_id: str | None) -> None:
        if self._selected_record_id == record_id:
            return
        previous = self._record_items.get(self._selected_record_id or "")
        if previous is not None:
            previous.set_selected(False)
        self._selected_record_id = record_id
        selected = self._record_items.get(record_id or "")
        if selected is not None:
            selected.set_selected(True)

    @property
    def selected_record_id(self) -> str | None:
        return self._selected_record_id

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
