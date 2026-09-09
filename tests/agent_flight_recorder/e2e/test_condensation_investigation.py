from flight_recorder.gui.controllers.selection import SelectionController
from flight_recorder.services.repository import resolve_condensation


def test_condensation_investigation_keeps_missing_evidence_navigable() -> None:
    condensation = resolve_condensation(
        {
            "forgotten_event_ids": ["recorded", "not-recorded"],
            "summary": "Prior work",
        },
        {"recorded"},
    )
    selection = SelectionController()
    selection.navigate_to_evidence(condensation.resolved_ids[0])

    assert condensation.unresolved_ids == ("not-recorded",)
    assert selection.current_selection().record_id == "recorded"
