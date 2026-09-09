from datetime import datetime

import pytest
from flight_recorder.models.envelopes import Record, RunStatus, Trace
from pydantic import ValidationError


def test_terminal_trace_requires_completion_fields() -> None:
    with pytest.raises(ValidationError):
        Trace(trace_id="trace", status=RunStatus.COMPLETED)

    trace = Trace(
        trace_id="trace",
        status=RunStatus.COMPLETED,
        completed_at=datetime.now(),
        stop_reason="agent_finished",
    )
    assert trace.stop_reason == "agent_finished"


def test_record_rejects_invalid_content_reference() -> None:
    with pytest.raises(ValidationError):
        Record(
            record_id="record",
            producer_id="producer",
            trace_id="trace",
            kind="run.started",
            content_ref="not-a-digest",
        )
