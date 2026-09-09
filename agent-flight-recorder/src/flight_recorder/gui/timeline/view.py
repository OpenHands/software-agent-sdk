from flight_recorder.gui.timeline.items import LABEL_WIDTH
from flight_recorder.gui.timeline.scene import TimelineScene
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPainter, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QGraphicsView


class TimelineViewWidget(QGraphicsView):
    span_activated = Signal(str)
    record_activated = Signal(str)
    zoom_changed = Signal(float)

    def __init__(self, scene: TimelineScene | None = None) -> None:
        self.timeline_scene = scene if scene is not None else TimelineScene()
        super().__init__(self.timeline_scene)
        self.zoom_factor = 1.0
        self.setObjectName("timeline")
        self.setAccessibleName("Agent activity timeline")
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.timeline_scene.span_activated.connect(self.span_activated.emit)
        self.timeline_scene.record_activated.connect(self.record_activated.emit)
        self.timeline_scene.sceneRectChanged.connect(self._update_minimap)
        self.horizontalScrollBar().valueChanged.connect(self._sync_floating_labels)
        self.minimap = QGraphicsView(self.timeline_scene, self)
        self.minimap.setObjectName("timeline-minimap")
        self.minimap.setAccessibleName("Timeline overview")
        self.minimap.setFixedSize(180, 72)
        self.minimap.setInteractive(False)
        self.minimap.setStyleSheet("border: 1px solid #9ba7a1; background: white;")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.minimap.move(
            max(8, self.viewport().width() - self.minimap.width() - 12),
            max(8, self.viewport().height() - self.minimap.height() - 12),
        )
        self._update_minimap()

    def _update_minimap(self) -> None:
        scene_rect = self.timeline_scene.sceneRect()
        if not scene_rect.isEmpty():
            self.minimap.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._sync_floating_labels()

    def _sync_floating_labels(self) -> None:
        self.timeline_scene.set_label_offset(max(0.0, self.mapToScene(0, 0).x()))

    def zoom_in(self) -> None:
        self._set_zoom(self.zoom_factor * 1.25, self.viewport().rect().center().x())

    def zoom_out(self) -> None:
        self._set_zoom(self.zoom_factor / 1.25, self.viewport().rect().center().x())

    def zoom_to_fit(self) -> None:
        self.resetTransform()
        self.zoom_factor = 1.0
        self.timeline_scene.set_time_zoom(1.0)
        self.horizontalScrollBar().setValue(0)
        self._sync_floating_labels()
        self.zoom_changed.emit(self.zoom_factor)

    def _set_zoom(self, zoom: float, anchor_x: int) -> None:
        zoom = min(20.0, max(0.25, zoom))
        if zoom == self.zoom_factor:
            return
        old_pixels_per_second = self.timeline_scene.pixels_per_second
        scene_x = self.mapToScene(anchor_x, 0).x()
        elapsed_at_anchor = max(
            0.0,
            (scene_x - LABEL_WIDTH) / max(old_pixels_per_second, 0.001),
        )
        self.zoom_factor = zoom
        self.timeline_scene.set_time_zoom(zoom)
        new_scene_x = (
            LABEL_WIDTH + elapsed_at_anchor * self.timeline_scene.pixels_per_second
        )
        self.horizontalScrollBar().setValue(round(new_scene_x - anchor_x))
        self._sync_floating_labels()
        self.zoom_changed.emit(self.zoom_factor)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        anchor_x = event.position().toPoint().x()
        if event.angleDelta().y() > 0:
            self._set_zoom(self.zoom_factor * 1.25, anchor_x)
        elif event.angleDelta().y() < 0:
            self._set_zoom(self.zoom_factor / 1.25, anchor_x)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (43, 61):
            self.zoom_in()
            return
        if event.key() == 45:
            self.zoom_out()
            return
        if event.key() == Qt.Key.Key_0:
            self.zoom_to_fit()
            return
        super().keyPressEvent(event)
