from dataclasses import dataclass

from flight_recorder.models.envelopes import Record
from flight_recorder.services.repository import _recorder_shutdown_record_ids


@dataclass(frozen=True)
class RecordInterval:
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class TimelineTimeProjection:
    intervals: dict[str, RecordInterval]
    elapsed_seconds: float


def project_record_intervals(records: list[Record]) -> TimelineTimeProjection:
    if not records:
        return TimelineTimeProjection(intervals={}, elapsed_seconds=0)

    timestamps = {
        record.record_id: (record.source_timestamp or record.observed_at).timestamp()
        for record in records
    }
    shutdown_record_ids = _recorder_shutdown_record_ids(tuple(records))
    activity_timestamps = [
        timestamp
        for record_id, timestamp in timestamps.items()
        if record_id not in shutdown_record_ids
    ]
    if shutdown_record_ids and activity_timestamps:
        last_activity_timestamp = max(activity_timestamps)
        for record_id in shutdown_record_ids:
            timestamps[record_id] = last_activity_timestamp
    origin = min(timestamps.values())
    starts = {
        record_id: max(0.0, timestamp - origin)
        for record_id, timestamp in timestamps.items()
    }
    intervals = {
        record.record_id: RecordInterval(
            start_seconds=starts[record.record_id],
            end_seconds=starts[record.record_id],
        )
        for record in records
    }

    actions_by_tool_call: dict[str, Record] = {}
    for record in sorted(
        records,
        key=lambda item: (starts[item.record_id], item.sequence or 0),
    ):
        event_type = record.payload.get("event_type")
        tool_call_id = record.payload.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            continue
        if event_type == "ActionEvent":
            actions_by_tool_call[tool_call_id] = record
        elif event_type == "ObservationEvent":
            action = actions_by_tool_call.pop(tool_call_id, None)
            if action is not None:
                start = starts[action.record_id]
                intervals[action.record_id] = RecordInterval(
                    start_seconds=start,
                    end_seconds=max(start, starts[record.record_id]),
                )

    return TimelineTimeProjection(
        intervals=intervals,
        elapsed_seconds=max(starts.values()),
    )
