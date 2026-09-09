from collections.abc import Sequence

from flight_recorder.models.view_models import RunSummary
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)


_EMPTY_INDEX = QModelIndex()


class RunTableModel(QAbstractTableModel):
    _HEADERS = ("Trace", "Status", "Records", "Cost")

    def __init__(
        self, runs: Sequence[RunSummary] = (), parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._all_runs = list(runs)
        self._runs = list(runs)

    def rowCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._runs)

    def columnCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._HEADERS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        run = self._runs[index.row()]
        values = (run.trace_id, run.status, run.record_count, f"${run.total_cost:.4f}")
        return values[index.column()]

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self._HEADERS[section]
        return None

    def set_runs(self, runs: Sequence[RunSummary]) -> None:
        self.beginResetModel()
        self._all_runs = list(runs)
        self._runs = list(runs)
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        needle = text.casefold()
        self.beginResetModel()
        self._runs = [
            run
            for run in self._all_runs
            if needle in run.trace_id.casefold() or needle in run.status.casefold()
        ]
        self.endResetModel()

    def run_at(self, row: int) -> RunSummary:
        return self._runs[row]
