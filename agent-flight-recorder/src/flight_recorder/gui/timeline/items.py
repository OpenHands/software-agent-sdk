from collections.abc import Callable

from flight_recorder.gui.timeline.layout import SpanLayout
from flight_recorder.gui.timeline.rows import (
    ACTIVITY_CATEGORY_TYPES_BY_KEY,
    classify_record,
)
from flight_recorder.gui.timeline.time import RecordInterval
from flight_recorder.models.envelopes import Record, RelationshipStatus
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsSceneMouseEvent,
)


SEQUENCE_WIDTH = 12.0
LANE_HEIGHT = 76.0
LABEL_WIDTH = 220.0
HEADER_HEIGHT = 42.0
MIN_RECORD_WIDTH = 8.0
RECORD_TOP = 44.0
RECORD_HEIGHT = 16.0


def record_width(interval: RecordInterval, pixels_per_second: float) -> float:
    return max(
        MIN_RECORD_WIDTH,
        interval.duration_seconds * pixels_per_second,
    )


class TimelineSpanItem(QGraphicsRectItem):
    def __init__(
        self,
        layout: SpanLayout,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        on_activated: Callable[[str], None] | None = None,
        pixels_per_second: float = 1.0,
        lane: int | None = None,
        row_top: float | None = None,
    ) -> None:
        start_seconds = float(layout.start) if start_seconds is None else start_seconds
        end_seconds = float(layout.end) if end_seconds is None else end_seconds
        width = max(
            8.0,
            (end_seconds - start_seconds) * pixels_per_second,
        )
        lane_top = (
            HEADER_HEIGHT + (layout.lane if lane is None else lane) * LANE_HEIGHT
            if row_top is None
            else row_top
        )
        super().__init__(
            QRectF(
                LABEL_WIDTH + start_seconds * pixels_per_second,
                lane_top + 10,
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
    if record.kind == "llm.request":
        return "LLM request"
    if record.kind == "llm.response":
        completion = record.payload.get("completion", record.payload)
        if isinstance(completion, dict) and isinstance(completion.get("error"), dict):
            return "LLM error"
        return "LLM response"
    event_type = record.payload.get("event_type")
    if event_type == "SystemPromptEvent":
        return "System prompt"
    if event_type == "MessageEvent":
        message = record.payload.get("llm_message")
        role = message.get("role") if isinstance(message, dict) else None
        if record.payload.get("source") == "user" or role == "user":
            return "User prompt"
        return "Assistant message"
    tool_name = record.payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name:
        return f"{event_type or record.kind}: {tool_name}"
    if isinstance(event_type, str) and event_type:
        return event_type
    return record.kind


def record_color(record: Record) -> QColor:
    return QColor(ACTIVITY_CATEGORY_TYPES_BY_KEY[classify_record(record)].color)


class TimelineSequenceArrowItem(QGraphicsPathItem):
    def __init__(
        self,
        previous_record_id: str,
        next_record_id: str,
        start: QPointF,
        end: QPointF,
    ) -> None:
        path = QPainterPath(start)
        if start.y() == end.y():
            path.lineTo(end)
        else:
            midpoint_x = (start.x() + end.x()) / 2
            path.lineTo(midpoint_x, start.y())
            path.lineTo(midpoint_x, end.y())
            path.lineTo(end)
        path.moveTo(end.x() - 4, end.y() - 3)
        path.lineTo(end)
        path.lineTo(end.x() - 4, end.y() + 3)
        super().__init__(path)
        self.previous_record_id = previous_record_id
        self.next_record_id = next_record_id
        self.start = start
        self.end = end
        self.setData(0, "record-sequence-arrow")
        self.setData(1, previous_record_id)
        self.setData(2, next_record_id)
        self.setPen(QPen(QColor("#68766f"), 1.25, Qt.PenStyle.SolidLine))
        self.setZValue(1)


class TimelineRecordItem(QGraphicsRectItem):
    def __init__(
        self,
        record: Record,
        interval: RecordInterval,
        *,
        lane: int,
        on_activated: Callable[[str], None],
        pixels_per_second: float,
        row_top: float | None = None,
        display_start_pixels: float | None = None,
        display_y: float | None = None,
    ) -> None:
        recorded_start_pixels = interval.start_seconds * pixels_per_second
        visible_start_pixels = (
            recorded_start_pixels
            if display_start_pixels is None
            else display_start_pixels
        )
        x = LABEL_WIDTH + visible_start_pixels
        width = record_width(interval, pixels_per_second)
        lane_top = HEADER_HEIGHT + lane * LANE_HEIGHT if row_top is None else row_top
        y = lane_top + RECORD_TOP if display_y is None else display_y
        super().__init__(QRectF(x, y, width, RECORD_HEIGHT))
        self.record_id = record.record_id
        self._on_activated = on_activated
        self.setBrush(QBrush(record_color(record)))
        self._default_pen = QPen(QColor("#f7f9f8"), 1)
        self._selected_pen = QPen(QColor("#f4b400"), 3)
        self._selected = False
        self.setPen(self._default_pen)
        self.setZValue(2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        tooltip = (
            f"{record_label(record)}\n"
            f"Recorded start: +{interval.start_seconds:.3f}s\n"
            f"Duration: {interval.duration_seconds:.3f}s\n"
            f"Kind: {record.kind}"
        )
        if visible_start_pixels != recorded_start_pixels:
            tooltip += (
                "\nDisplay-aligned start: "
                f"+{visible_start_pixels / pixels_per_second:.3f}s"
            )
        self.setToolTip(tooltip + "\nClick to inspect the recorded payload")

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setPen(self._selected_pen if selected else self._default_pen)
        self.setZValue(5 if selected else 2)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self._on_activated(self.record_id)
        super().mousePressEvent(event)
