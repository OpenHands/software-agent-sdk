from flight_recorder.gui.controllers.selection import SelectionController
from flight_recorder.models.view_models import Selection


def test_selection_controller_emits_only_canonical_changes(qtbot) -> None:
    controller = SelectionController()
    selections: list[Selection] = []
    controller.selection_changed.connect(selections.append)

    controller.select_record("record-1")
    controller.select_record("record-1")
    controller.select_span("span-1")
    controller.select_span("span-1")

    assert selections == [
        Selection(record_id="record-1"),
        Selection(span_id="span-1"),
    ]
    assert controller.current_selection() == Selection(span_id="span-1")
