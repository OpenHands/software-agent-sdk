from flight_recorder.collector.queue import RecordQueue
from flight_recorder.models.envelopes import Record


def record(kind: str, identity: str) -> Record:
    return Record(
        record_id=identity,
        producer_id="producer",
        trace_id="trace",
        kind=kind,
    )


def test_queue_discards_stream_delta_before_lifecycle_record() -> None:
    queue = RecordQueue(maxsize=1)
    assert queue.put(record("llm.stream_delta", "delta"))
    assert queue.put(record("run.finished", "finish"))

    assert [item.record_id for item in queue.drain()] == ["finish"]
    assert queue.dropped == 1


def test_queue_never_blocks_when_full() -> None:
    queue = RecordQueue(maxsize=1)
    assert queue.put(record("run.started", "start"))
    assert not queue.put(record("llm.stream_delta", "delta"))


def test_queue_never_partially_enqueues_llm_exchange() -> None:
    queue = RecordQueue(maxsize=1)
    exchange = (
        record("llm.request", "request"),
        record("llm.response", "response"),
    )

    assert not queue.put_many(exchange)
    assert queue.drain() == []
    assert queue.dropped == 2
