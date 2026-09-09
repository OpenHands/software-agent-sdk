from datetime import datetime, timedelta

from flight_recorder.diagnostics.rules import fingerprint, repeated_tool_calls
from flight_recorder.models.envelopes import Record


def tool_record(record_id: str, sequence: int, command: str) -> Record:
    return Record(
        record_id=record_id,
        producer_id="producer",
        trace_id="trace",
        sequence=sequence,
        observed_at=datetime(2026, 9, 1, 12) + timedelta(seconds=sequence),
        kind="tool.request",
        payload={"tool": "terminal", "command": command, "id": record_id},
    )


def test_fingerprint_ignores_volatile_identity_but_preserves_arguments() -> None:
    first = tool_record("first", 1, "pytest")
    repeated = tool_record("second", 2, "pytest")
    different = tool_record("third", 3, "ruff check")

    assert fingerprint(first) == fingerprint(repeated)
    assert fingerprint(first) != fingerprint(different)


def test_repeated_tool_call_threshold_is_inclusive() -> None:
    records = [tool_record("first", 1, "pytest"), tool_record("second", 2, "pytest")]

    assert repeated_tool_calls(records, threshold=2)
    assert repeated_tool_calls(records, threshold=3) == []
