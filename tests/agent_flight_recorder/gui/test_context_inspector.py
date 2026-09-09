from flight_recorder.gui.controllers.selection import SelectionController
from flight_recorder.gui.inspector.context import ContextInspector
from flight_recorder.models.envelopes import Provenance, ProvenanceKind
from flight_recorder.models.view_models import CondensationView, ContextView


def test_context_inspector_labels_provenance_and_unresolved_events(qtbot) -> None:
    inspector = ContextInspector()
    qtbot.addWidget(inspector)
    context = ContextView(
        record_id="request",
        messages=({"role": "user", "content": "Hello"},),
        provenance=Provenance(
            kind=ProvenanceKind.RECONSTRUCTED,
            source_ids=("message",),
            complete=False,
        ),
    )
    condensation = CondensationView(
        resolved_ids=("message",),
        unresolved_ids=("missing",),
        summary="Earlier work",
    )

    inspector.set_context(context)
    inspector.set_condensation(condensation)

    assert inspector.provenance.text() == "Reconstructed context"
    assert "missing" in inspector.condensation.toPlainText()


def test_context_source_navigation_uses_canonical_selection(qtbot) -> None:
    controller = SelectionController()

    controller.navigate_to_evidence("message")

    assert controller.current_selection().record_id == "message"
