"""Single-writer append and index collector."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event, Thread

from flight_recorder.collector.normalize import make_record
from flight_recorder.collector.queue import RecordQueue
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import CommittedBatch, Record


class Collector:
    def __init__(self, queue: RecordQueue, bundle: Path, index: TraceIndex) -> None:
        self.queue = queue
        self.bundle = bundle
        self.index = index
        self._stop = Event()
        self._thread: Thread | None = None
        self._sequence = 0
        self._record_ids: set[str] = set()
        self._reported_drops = 0
        self._subscribers: list[Callable[[CommittedBatch], None]] = []
        self.errors = 0

    def start(self) -> None:
        self.bundle.mkdir(parents=True, exist_ok=True)
        self._thread = Thread(target=self._run, name="flight-recorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def subscribe(self, callback: Callable[[CommittedBatch], None]) -> None:
        self._subscribers.append(callback)

    def _run(self) -> None:
        while not self._stop.wait(0.05) or len(self.queue):
            records = self.queue.drain()
            if records:
                if self.queue.dropped > self._reported_drops:
                    dropped_records = self.queue.dropped - self._reported_drops
                    self._reported_drops = self.queue.dropped
                    records.append(
                        make_record(
                            producer_id=records[0].producer_id,
                            trace_id=records[0].trace_id,
                            kind="recorder.warning",
                            payload={"dropped_records": dropped_records},
                        )
                    )
                try:
                    self._commit(records)
                except Exception:
                    self.errors += 1

    def _commit(self, records: list[Record]) -> None:
        unique_records: list[Record] = []
        batch_ids: set[str] = set()
        for record in records:
            if (
                record.record_id not in self._record_ids
                and record.record_id not in batch_ids
            ):
                unique_records.append(record)
                batch_ids.add(record.record_id)
        if not unique_records:
            return

        committed: list[Record] = []
        for record in unique_records:
            self._sequence += 1
            committed.append(record.model_copy(update={"sequence": self._sequence}))
        path = self.bundle / "records.jsonl"
        manifest = self.bundle / "manifest.json"
        if not manifest.exists():
            manifest.write_text(
                json.dumps(
                    {
                        "format": "agent-flight-recorder",
                        "format_version": 1,
                        "trace_id": committed[0].trace_id,
                        "created_at": datetime.now().isoformat(),
                        "status": "running",
                        "recorder_version": "0.1.0",
                        "content_encoding": "zstd",
                    },
                    indent=2,
                )
                + "\n"
            )
        with path.open("a") as stream:
            for record in committed:
                stream.write(record.model_dump_json() + "\n")
            stream.flush()
        self.index.add_records(committed)
        self._record_ids.update(batch_ids)
        batch = CommittedBatch(
            trace_id=committed[0].trace_id,
            first_sequence=committed[0].sequence or 1,
            last_sequence=committed[-1].sequence or 1,
            record_ids=tuple(record.record_id for record in committed),
        )
        for callback in tuple(self._subscribers):
            try:
                callback(batch)
            except Exception:
                continue
