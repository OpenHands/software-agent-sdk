import json
from pathlib import Path

from flight_recorder.models.database import TraceIndex
from flight_recorder.recorder import Recorder

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.llm import Metrics


def test_bad_callback_data_does_not_escape(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "trace.afr", TraceIndex(tmp_path / "index.db"))

    recorder.on_event(object())
    recorder.close()

    assert recorder.errors == 1


def test_llm_request_is_committed_without_waiting_for_completion(
    tmp_path: Path,
) -> None:
    index = TraceIndex(tmp_path / "index.db")
    recorder = Recorder(tmp_path / "trace.afr", index)

    recorder.on_llm_request(
        "call-1",
        json.dumps(
            {
                "llm_call_id": "call-1",
                "timestamp": 1_788_545_603.75,
                "messages": [{"role": "user", "content": "hello"}],
            }
        ),
    )
    recorder.close()

    requests = [
        record
        for record in index.iter_records(recorder.trace_id)
        if record.kind == "llm.request"
    ]
    assert len(requests) == 1
    assert requests[0].llm_call_id == "call-1"
    assert requests[0].payload["request"]["messages"][0]["content"] == "hello"


def test_metrics_capture_and_close_are_failure_isolated(tmp_path: Path) -> None:
    index = TraceIndex(tmp_path / "index.db")
    recorder = Recorder(tmp_path / "trace.afr", index)
    stats = ConversationStats(
        usage_to_metrics={"agent": Metrics(model_name="test-model")}
    )

    recorder.record_metrics(stats)
    recorder.close()
    recorder.close()
    recorder.record_metrics(stats)

    records = index.iter_records(recorder.trace_id)
    assert [record.kind for record in records] == [
        "run.started",
        "agent.started",
        "metrics.snapshot",
        "agent.finished",
        "run.finished",
    ]
    assert recorder.errors == 1


def test_storage_failure_does_not_escape_collector_thread(
    tmp_path: Path, monkeypatch
) -> None:
    index = TraceIndex(tmp_path / "index.db")

    def fail_add_records(records) -> None:
        raise OSError("injected index failure")

    monkeypatch.setattr(index, "add_records", fail_add_records)
    recorder = Recorder(tmp_path / "trace.afr", index)

    recorder.close()

    assert recorder.warning_counters["collector_errors"] == 1
