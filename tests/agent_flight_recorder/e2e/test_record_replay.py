from pathlib import Path

from flight_recorder.models.database import TraceIndex
from flight_recorder.recorder import Recorder
from flight_recorder.services.bundles import BundleService
from flight_recorder.services.repository import TraceRepository

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.llm import Metrics
from tests.agent_flight_recorder.fixtures.conversation import (
    run_deterministic_conversation,
)


def test_record_and_replay_preserves_run_and_usage(tmp_path: Path) -> None:
    bundle = tmp_path / "recorded.afr"
    source_index = TraceIndex(tmp_path / "source.db")
    recorder = Recorder(bundle, source_index)
    metrics = Metrics(model_name="test-model")
    metrics.add_cost(0.25)
    metrics.add_token_usage(10, 4, 2, 1, 128, "response-1")

    run_deterministic_conversation(
        tmp_path / "workspace", callbacks=[recorder.on_event]
    )
    recorder.record_metrics(ConversationStats(usage_to_metrics={"agent": metrics}))
    recorder.close()

    source_records = source_index.iter_records(recorder.trace_id)
    archive = BundleService().export_bundle(bundle, tmp_path / "recorded.afr.zip")
    replay_index = TraceIndex(tmp_path / "replay.db")
    BundleService(replay_index).import_bundle(archive)
    replay_records = replay_index.iter_records(recorder.trace_id)

    assert replay_records == source_records
    assert [record.sequence for record in replay_records] == list(
        range(1, len(replay_records) + 1)
    )
    assert replay_records[0].kind == "run.started"
    assert replay_records[-1].kind == "run.finished"

    run = TraceRepository(replay_index).get_run(recorder.trace_id)
    assert run.status == "completed"
    assert run.record_count == len(replay_records)
    assert run.total_usage.prompt_tokens == 10
    assert run.total_usage.completion_tokens == 4
    assert run.total_cost == 0.25
