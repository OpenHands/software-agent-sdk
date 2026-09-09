from html import escape

from flight_recorder.gui.inspector.summary import format_readable_data
from flight_recorder.models.envelopes import ProvenanceKind
from flight_recorder.models.view_models import CondensationView, ContextView
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget


class ContextInspector(QWidget):
    evidence_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.provenance = QLabel("No context")
        self.context = QTextBrowser()
        self.condensation = QTextBrowser()
        self.context.setOpenLinks(False)
        self.condensation.setOpenLinks(False)
        self.context.anchorClicked.connect(
            lambda url: self.evidence_requested.emit(url.toString())
        )
        self.condensation.anchorClicked.connect(
            lambda url: self.evidence_requested.emit(url.toString())
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self.provenance)
        layout.addWidget(self.context)
        layout.addWidget(self.condensation)

    def set_context(self, context: ContextView) -> None:
        label = (
            "Exact captured context"
            if context.provenance.kind is ProvenanceKind.CAPTURED
            else "Reconstructed context"
        )
        self.provenance.setText(label)
        source_links = " ".join(
            f'<a href="{source_id}">{source_id}</a>'
            for source_id in context.provenance.source_ids
        )
        messages = escape(format_readable_data(list(context.messages)))
        self.context.setHtml(f"<p>{source_links}</p><pre>{messages}</pre>")

    def set_condensation(self, condensation: CondensationView) -> None:
        resolved = " ".join(
            f'<a href="{record_id}">{record_id}</a>'
            for record_id in condensation.resolved_ids
        )
        unresolved = escape(", ".join(condensation.unresolved_ids) or "None")
        summary = escape(
            condensation.summary or "No replacement summary recorded"
        ).replace("\n", "<br>")
        self.condensation.setHtml(
            f"<p>Forgotten events: {resolved}</p>"
            f"<p>Unresolved: {unresolved}</p><p>Replacement: {summary}</p>"
        )
