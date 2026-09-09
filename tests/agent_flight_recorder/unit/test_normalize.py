import json

from flight_recorder.adapter.conversation import normalize_event
from flight_recorder.adapter.llm import normalize_completion_log, normalize_metrics
from flight_recorder.collector.normalize import make_record

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import Metrics


def test_completion_log_preserves_response_correlation() -> None:
    record = normalize_completion_log(
        "completion.json",
        json.dumps({"response_id": "response-1", "model": "test"}),
        producer_id="producer",
        trace_id="trace",
    )

    assert record.kind == "llm.response"
    assert record.llm_response_id == "response-1"


def test_condensation_preserves_response_correlation() -> None:
    event = Condensation(
        id="condensation-1",
        timestamp="2026-09-01T12:00:00",
        forgotten_event_ids={"event-2", "event-1"},
        summary="Earlier work",
        summary_offset=1,
        llm_response_id="response-1",
    )

    record = normalize_event(event, producer_id="producer", trace_id="trace")

    assert record.kind == "context.condensed"
    assert record.openhands_event_id == "condensation-1"
    assert record.llm_response_id == "response-1"
    assert record.payload["forgotten_event_ids"] == ["event-1", "event-2"]


def test_metrics_normalization_uses_bounded_snapshots() -> None:
    metrics = Metrics(model_name="test-model")
    metrics.add_cost(0.25)
    metrics.add_token_usage(
        prompt_tokens=10,
        completion_tokens=4,
        cache_read_tokens=2,
        cache_write_tokens=1,
        context_window=128,
        response_id="response-1",
    )
    stats = ConversationStats(usage_to_metrics={"agent": metrics})

    record = normalize_metrics(stats, producer_id="producer", trace_id="trace")

    assert record.kind == "metrics.snapshot"
    snapshot = record.payload["usage_to_metrics"]["agent"]
    assert snapshot["accumulated_cost"] == 0.25
    assert snapshot["accumulated_token_usage"]["prompt_tokens"] == 10
    assert "costs" not in snapshot
    assert "token_usages" not in snapshot


def test_make_record_assigns_stable_source_fields() -> None:
    record = make_record(
        producer_id="producer",
        trace_id="trace",
        kind="run.started",
        payload={},
    )

    assert record.producer_id == "producer"
    assert record.trace_id == "trace"
    assert record.record_id
