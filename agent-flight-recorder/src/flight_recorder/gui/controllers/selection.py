from flight_recorder.models.view_models import Selection
from PySide6.QtCore import QObject, Signal


class SelectionController(QObject):
    selection_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._selection = Selection()

    def select_record(self, record_id: str) -> None:
        self._set_selection(Selection(record_id=record_id))

    def select_span(self, span_id: str) -> None:
        self._set_selection(Selection(span_id=span_id))

    def navigate_to_evidence(self, record_id: str) -> None:
        self.select_record(record_id)

    def current_selection(self) -> Selection:
        return self._selection

    def clear(self) -> None:
        self._set_selection(Selection())

    def _set_selection(self, selection: Selection) -> None:
        if selection == self._selection:
            return
        self._selection = selection
        self.selection_changed.emit(selection)
