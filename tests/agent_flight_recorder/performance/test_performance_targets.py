from datetime import datetime
from statistics import quantiles
from threading import Event
from time import perf_counter, sleep

from flight_recorder.collector.queue import RecordQueue
from flight_recorder.gui.controllers.selection import SelectionController
from flight_recorder.gui.timeline.layout import project_spans
from flight_recorder.models.envelopes import CommittedBatch, Record, Span
from flight_recorder.services.subscriptions import TraceSubscription


def record(sequence: int) -> Record:
    return Record(
        record_id=f"record-{sequence}",
        producer_id="benchmark",
        trace_id="trace",
        sequence=sequence,
        observed_at=datetime(2026, 9, 1, 12),
        kind="llm.stream_delta",
    )


def test_callback_latency_and_observed_run_overhead() -> None:
    queue = RecordQueue(maxsize=256)
    latencies = []
    observed_work_seconds = 0.5
    for sequence in range(1, 51):
        sleep(0.01)
        callback_started = perf_counter()
        queue.put(record(sequence))
        latencies.append(perf_counter() - callback_started)

    assert quantiles(latencies, n=20)[-1] < 0.005
    assert sum(latencies) / observed_work_seconds < 0.05


def test_live_update_latency_is_below_500ms() -> None:
    delivered = Event()
    subscription = TraceSubscription(max_workers=1)
    subscription.subscribe("trace", 0, lambda batch: delivered.set())
    started = perf_counter()
    subscription.publish(
        CommittedBatch(
            trace_id="trace", first_sequence=1, last_sequence=1, record_ids=("one",)
        )
    )

    assert delivered.wait(0.5)
    assert perf_counter() - started < 0.5
    subscription.close()


def test_100k_overview_and_selection_targets() -> None:
    spans = [
        Span(
            span_id=f"span-{index}",
            trace_id="trace",
            span_type="iteration",
            name=f"Iteration {index}",
            agent_span_id=f"agent-{index % 8}",
            start_sequence=index + 1,
            end_sequence=index + 1,
            started_at=datetime(2026, 9, 1, 12),
            status="completed",
        )
        for index in range(100_000)
    ]
    started = perf_counter()
    projected = project_spans(spans, current_sequence=100_000)
    render_elapsed = perf_counter() - started
    controller = SelectionController()
    selection_started = perf_counter()
    controller.select_span(projected[-1].span_id)
    selection_elapsed = perf_counter() - selection_started

    assert len(projected) == 100_000
    assert render_elapsed < 3
    assert selection_elapsed < 0.1
