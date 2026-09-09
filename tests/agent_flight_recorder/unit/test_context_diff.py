from flight_recorder.gui.inspector.diff import diff_contexts
from flight_recorder.services.repository import resolve_condensation


def test_context_diff_reports_added_removed_and_changed_messages() -> None:
    previous = (
        {"id": "one", "content": "old"},
        {"id": "removed", "content": "gone"},
    )
    current = (
        {"id": "one", "content": "new"},
        {"id": "added", "content": "here"},
    )

    difference = diff_contexts(previous, current)

    assert difference.added == ("added",)
    assert difference.removed == ("removed",)
    assert difference.changed == ("one",)


def test_condensation_retains_unresolved_event_ids() -> None:
    result = resolve_condensation(
        {"forgotten_event_ids": ["known", "missing"], "summary": "Earlier work"},
        known_record_ids={"known"},
    )

    assert result.resolved_ids == ("known",)
    assert result.unresolved_ids == ("missing",)
    assert result.summary == "Earlier work"
