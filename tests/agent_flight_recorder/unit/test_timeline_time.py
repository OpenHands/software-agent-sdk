from datetime import datetime, timedelta

from flight_recorder.gui.timeline.time import project_record_intervals
from flight_recorder.models.envelopes import Record


BASE = datetime(2026, 9, 2, 10, 0, 0)


def timed_record(
    record_id: str,
    sequence: int,
    event_type: str,
    seconds: float,
    *,
    tool_call_id: str | None = None,
    observed_offset: float = 0,
) -> Record:
    payload = {"event_type": event_type}
    if tool_call_id is not None:
        payload["tool_call_id"] = tool_call_id
    return Record(
        record_id=record_id,
        producer_id="producer",
        trace_id="trace",
        sequence=sequence,
        observed_at=BASE + timedelta(seconds=seconds + observed_offset),
        source_timestamp=BASE + timedelta(seconds=seconds),
        kind="conversation.event",
        payload=payload,
    )


def test_action_interval_uses_source_time_until_correlated_result() -> None:
    records = [
        timed_record("message", 1, "MessageEvent", 0),
        timed_record("action", 2, "ActionEvent", 2, tool_call_id="call-1"),
        timed_record("result", 3, "ObservationEvent", 5.5, tool_call_id="call-1"),
    ]

    projection = project_record_intervals(records)

    assert projection.elapsed_seconds == 5.5
    assert projection.intervals["action"].start_seconds == 2
    assert projection.intervals["action"].end_seconds == 5.5
    assert projection.intervals["action"].duration_seconds == 3.5
    assert projection.intervals["result"].duration_seconds == 0


def test_timing_prefers_source_timestamp_and_falls_back_to_observed() -> None:
    source_timed = timed_record("source", 1, "MessageEvent", 1, observed_offset=20)
    observed_timed = Record(
        record_id="observed",
        producer_id="producer",
        trace_id="trace",
        sequence=2,
        observed_at=BASE + timedelta(seconds=4),
        kind="conversation.event",
        payload={"event_type": "MessageEvent"},
    )

    projection = project_record_intervals([source_timed, observed_timed])

    assert projection.intervals["source"].start_seconds == 0
    assert projection.intervals["observed"].start_seconds == 3


def test_recorder_shutdown_idle_time_does_not_extend_timeline() -> None:
    records = [
        Record(
            record_id="run-start",
            producer_id="producer",
            trace_id="trace",
            sequence=1,
            observed_at=BASE,
            kind="run.started",
        ),
        Record(
            record_id="agent-start",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=2,
            observed_at=BASE,
            kind="agent.started",
        ),
        timed_record("activity", 3, "MessageEvent", 30),
        Record(
            record_id="agent-finish",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=4,
            observed_at=BASE + timedelta(hours=1),
            kind="agent.finished",
        ),
        Record(
            record_id="run-finish",
            producer_id="producer",
            trace_id="trace",
            sequence=5,
            observed_at=BASE + timedelta(hours=1),
            kind="run.finished",
            payload={"stop_reason": "recorder_closed"},
        ),
    ]

    projection = project_record_intervals(records)

    assert projection.elapsed_seconds == 30
    assert projection.intervals["agent-finish"].start_seconds == 30
    assert projection.intervals["run-finish"].start_seconds == 30
