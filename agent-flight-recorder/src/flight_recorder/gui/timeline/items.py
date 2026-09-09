from collections.abc import Callable

from flight_recorder.gui.timeline.layout import SpanLayout
from flight_recorder.gui.timeline.rows import (
    ACTIVITY_ROW_TYPES_BY_KEY,
    classify_record,
)
from flight_recorder.gui.timeline.time import RecordInterval
from flight_recorder.models.envelopes import Record, RelationshipStatus
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsSceneMouseEvent,
)


SEQUENCE_WIDTH = 12.0
LANE_HEIGHT = 76.0
LABEL_WIDTH = 220.0
HEADER_HEIGHT = 42.0


class TimelineSpanItem(QGraphicsRectItem):
    def __init__(
        self,
        layout: SpanLayout,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        on_activated: Callable[[str], None] | None = None,
        pixels_per_second: float = 1.0,
        lane: int | None = None,
    ) -> None:
        start_seconds = float(layout.start) if start_seconds is None else start_seconds
        end_seconds = float(layout.end) if end_seconds is None else end_seconds
        width = max(
            8.0,
            (end_seconds - start_seconds) * pixels_per_second,
        )
        super().__init__(
            QRectF(
                LABEL_WIDTH + start_seconds * pixels_per_second,
                HEADER_HEIGHT
                + (layout.lane if lane is None else lane) * LANE_HEIGHT
                + 10,
                width,
                28,
            )
        )
        self.span_id = layout.span_id
        self._on_activated = on_activated
        self.setToolTip(
            f"{layout.span_id} ({layout.relationship_status.value} relationship)"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setBrush(QColor("#c96d3b") if layout.incomplete else QColor("#2f7d67"))
        pen = QPen(QColor("#1f2926"), 1)
        if layout.relationship_status is RelationshipStatus.INFERRED:
            pen.setStyle(Qt.PenStyle.DashLine)
        elif layout.relationship_status is RelationshipStatus.UNRESOLVED:
            pen.setStyle(Qt.PenStyle.DotLine)
            pen.setWidth(2)
        self.setPen(pen)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._on_activated is not None:
            self._on_activated(self.span_id)
        super().mousePressEvent(event)


def record_label(record: Record) -> str:
    event_type = record.payload.get("event_type")
    tool_name = record.payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name:
        return f"{event_type or record.kind}: {tool_name}"
    if isinstance(event_type, str) and event_type:
        return event_type
    return record.kind


def record_color(record: Record) -> QColor:
    return QColor(ACTIVITY_ROW_TYPES_BY_KEY[classify_record(record)].color)


class TimelineRecordItem(QGraphicsRectItem):
    def __init__(
        self,
        record: Record,
        interval: RecordInterval,
        *,
        lane: int,
        on_activated: Callable[[str], None],
        pixels_per_second: float,
    ) -> None:
        x = LABEL_WIDTH + interval.start_seconds * pixels_per_second
        width = max(
            2.0,
            interval.duration_seconds * pixels_per_second,
        )
        y = HEADER_HEIGHT + lane * LANE_HEIGHT + 44
        super().__init__(QRectF(x, y, width, 16))
        self.record_id = record.record_id
        self._on_activated = on_activated
        self.setBrush(QBrush(record_color(record)))
        self.setPen(QPen(QColor("#f7f9f8"), 1))
        self.setZValue(2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setToolTip(
            f"{record_label(record)}\n"
            f"Start: +{interval.start_seconds:.3f}s\n"
            f"Duration: {interval.duration_seconds:.3f}s\n"
            f"Kind: {record.kind}\nClick to inspect the recorded payload"
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self._on_activated(self.record_id)
        super().mousePressEvent(event)
