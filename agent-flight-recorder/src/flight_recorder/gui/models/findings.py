from collections.abc import Sequence

from flight_recorder.models.envelopes import Finding
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)


_EMPTY_INDEX = QModelIndex()


class FindingsModel(QAbstractTableModel):
    _HEADERS = ("Severity", "Category", "Finding", "Evidence")

    def __init__(
        self, findings: Sequence[Finding] = (), parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._all_findings = list(findings)
        self._findings = list(findings)

    def rowCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._findings)

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
        finding = self._findings[index.row()]
        values = (
            finding.severity,
            finding.category,
            finding.title,
            ", ".join(finding.evidence_ids),
        )
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

    def set_findings(self, findings: Sequence[Finding]) -> None:
        self.beginResetModel()
        self._all_findings = list(findings)
        self._findings = list(findings)
        self.endResetModel()

    def set_filters(self, *, severity: str | None, category: str | None) -> None:
        self.beginResetModel()
        self._findings = [
            finding
            for finding in self._all_findings
            if (severity is None or finding.severity == severity)
            and (category is None or finding.category == category)
        ]
        self.endResetModel()

    def finding_at(self, row: int) -> Finding:
        return self._findings[row]

    def evidence_at(self, row: int) -> tuple[str, ...]:
        return self.finding_at(row).evidence_ids
