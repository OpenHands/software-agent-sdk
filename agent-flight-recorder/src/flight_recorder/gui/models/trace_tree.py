from flight_recorder.gui.timeline.items import record_label
from flight_recorder.models.view_models import TimelineView
from PySide6.QtCore import QModelIndex, QObject
from PySide6.QtGui import QStandardItem, QStandardItemModel


class TraceTreeModel(QStandardItemModel):
    def __init__(
        self, timeline: TimelineView | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.setHorizontalHeaderLabels(["Activity"])
        if timeline is not None:
            self.set_timeline(timeline)

    def set_timeline(self, timeline: TimelineView) -> None:
        self.removeRows(0, self.rowCount())
        has_agent_hierarchy = any(
            span.span_type == "agent" and span.parent_span_id is not None
            for span in timeline.spans
        )
        if not has_agent_hierarchy:
            for record in timeline.records:
                item = QStandardItem(record_label(record))
                item.setData(record.record_id)
                self.invisibleRootItem().appendRow(item)
            return

        span_items: dict[str, QStandardItem] = {}
        for span in sorted(
            timeline.spans, key=lambda item: (item.start_sequence, item.span_id)
        ):
            item = QStandardItem(span.name)
            item.setData(span.span_id)
            item.setToolTip(f"{span.relationship_status.value} relationship")
            span_items[span.span_id] = item
        for span in sorted(
            timeline.spans, key=lambda item: (item.start_sequence, item.span_id)
        ):
            item = span_items[span.span_id]
            parent = span_items.get(span.parent_span_id or "")
            (parent or self.invisibleRootItem()).appendRow(item)

        for record in timeline.records:
            item = QStandardItem(record_label(record))
            item.setData(record.record_id)
            parent = span_items.get(record.span_id or record.parent_span_id or "")
            (parent or self.invisibleRootItem()).appendRow(item)

    def index_for_id(self, item_id: str) -> QModelIndex:
        pending = [self.invisibleRootItem()]
        while pending:
            parent = pending.pop()
            for row in range(parent.rowCount()):
                item = parent.child(row)
                if item.data() == item_id:
                    return item.index()
                pending.append(item)
        return QModelIndex()
