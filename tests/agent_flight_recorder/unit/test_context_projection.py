from flight_recorder.models.envelopes import ProvenanceKind, Record
from flight_recorder.services.repository import TraceRepository


def test_context_prefers_authoritative_completion_log(trace_index) -> None:
    trace_index.add_records(
        [
            Record(
                record_id="request",
                producer_id="producer",
                trace_id="trace",
                sequence=1,
                kind="llm.response",
                payload={
                    "completion": {
                        "messages": [{"role": "user", "content": "Exact input"}]
                    }
                },
            )
        ]
    )

    context = TraceRepository(trace_index).get_model_context("trace", "request")

    assert context.provenance.kind == ProvenanceKind.CAPTURED
    assert context.messages[0]["content"] == "Exact input"


def test_context_reconstructs_from_conversation_events(trace_index) -> None:
    trace_index.add_records(
        [
            Record(
                record_id="message",
                producer_id="producer",
                trace_id="trace",
                sequence=1,
                kind="conversation.event",
                payload={
                    "event_type": "MessageEvent",
                    "llm_message": {"role": "user", "content": [{"text": "Hello"}]},
                },
            ),
            Record(
                record_id="request",
                producer_id="producer",
                trace_id="trace",
                sequence=2,
                kind="llm.response",
            ),
        ]
    )

    context = TraceRepository(trace_index).get_model_context("trace", "request")

    assert context.provenance.kind == ProvenanceKind.RECONSTRUCTED
    assert context.provenance.source_ids == ("message",)
    assert context.messages[0]["role"] == "user"
