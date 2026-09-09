from datetime import datetime

from flight_recorder.diagnostics.rules import no_progress_iterations, recurring_failures
from flight_recorder.models.envelopes import Record


def diagnostic_record(
    record_id: str, sequence: int, kind: str, payload: dict
) -> Record:
    return Record(
        record_id=record_id,
        producer_id="producer",
        trace_id="trace",
        sequence=sequence,
        observed_at=datetime(2026, 9, 1, 12),
        kind=kind,
        payload=payload,
    )


def test_recurring_failure_requires_matching_failures_at_threshold() -> None:
    failures = [
        diagnostic_record(
            f"error-{index}", index, "tool.error", {"error": "command failed"}
        )
        for index in (1, 2)
    ]

    assert recurring_failures(failures, threshold=2)[0].evidence_ids == (
        "error-1",
        "error-2",
    )
    assert recurring_failures(failures, threshold=3) == []


def test_no_progress_requires_consecutive_unchanged_iterations() -> None:
    records = [
        diagnostic_record(
            f"iteration-{index}",
            index,
            "iteration.finished",
            {"made_progress": made_progress},
        )
        for index, made_progress in enumerate((False, False, True, False), start=1)
    ]

    findings = no_progress_iterations(records, threshold=2)

    assert len(findings) == 1
    assert findings[0].evidence_ids == ("iteration-1", "iteration-2")
