from pathlib import Path

from flight_recorder.collector.persistence import Collector
from flight_recorder.collector.queue import RecordQueue
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import Record


def test_collector_assigns_order_and_publishes_batch(tmp_path: Path) -> None:
    queue = RecordQueue()
    index = TraceIndex(tmp_path / "index.db")
    collector = Collector(queue, tmp_path / "trace.afr", index)
    batches = []
    collector.subscribe(batches.append)
    queue.put(
        Record(
            record_id="one",
            producer_id="producer",
            trace_id="trace",
            kind="run.started",
        )
    )

    collector.start()
    collector.stop()

    assert index.iter_records("trace")[0].sequence == 1
    assert batches[0].record_ids == ("one",)


def test_collector_deduplicates_record_ids_before_append(tmp_path: Path) -> None:
    queue = RecordQueue()
    index = TraceIndex(tmp_path / "index.db")
    bundle = tmp_path / "trace.afr"
    collector = Collector(queue, bundle, index)
    batches = []
    collector.subscribe(batches.append)
    record = Record(
        record_id="duplicate",
        producer_id="producer",
        trace_id="trace",
        kind="run.started",
    )
    queue.put(record)
    queue.put(record)

    collector.start()
    collector.stop()

    assert len((bundle / "records.jsonl").read_text().splitlines()) == 1
    assert [item.record_id for item in index.iter_records("trace")] == ["duplicate"]
    assert batches[0].record_ids == ("duplicate",)


def test_collector_records_queue_omissions(tmp_path: Path) -> None:
    queue = RecordQueue(maxsize=1)
    index = TraceIndex(tmp_path / "index.db")
    collector = Collector(queue, tmp_path / "trace.afr", index)
    queue.put(
        Record(
            record_id="stream",
            producer_id="producer",
            trace_id="trace",
            kind="llm.stream_delta",
        )
    )
    queue.put(
        Record(
            record_id="lifecycle",
            producer_id="producer",
            trace_id="trace",
            kind="run.finished",
        )
    )

    collector.start()
    collector.stop()

    records = index.iter_records("trace")
    assert [record.kind for record in records] == ["run.finished", "recorder.warning"]
    assert records[-1].payload == {"dropped_records": 1}
