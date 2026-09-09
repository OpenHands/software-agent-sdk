from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import Record


def test_index_orders_and_deduplicates_records(trace_index: TraceIndex) -> None:
    record = Record(
        record_id="one",
        producer_id="producer",
        trace_id="trace",
        sequence=1,
        kind="run.started",
    )
    trace_index.add_records([record, record])

    assert trace_index.iter_records("trace") == [record]


def test_index_creates_every_rebuildable_projection_table(
    trace_index: TraceIndex,
) -> None:
    with trace_index.connect() as connection:
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "runs",
        "spans",
        "records",
        "llm_calls",
        "artifacts",
        "findings",
        "content_blobs",
    } <= names
