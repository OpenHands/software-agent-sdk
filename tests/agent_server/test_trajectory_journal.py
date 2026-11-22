import asyncio
import gzip
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.api_core.exceptions import NotFound, ServiceUnavailable
from google.cloud import storage
from litellm import ModelResponse
from litellm.exceptions import RateLimitError
from pydantic import SecretStr

from openhands.agent_server.persistence.trajectory_archive import (
    ArchiveGap,
    DirectoryStore,
    ObjectRef,
    coverage,
    read_seal,
    read_segments,
    seal_journal,
)
from openhands.agent_server.persistence.trajectory_gcs import GCSStore
from openhands.agent_server.persistence.trajectory_validation import (
    Admission,
    Candidate,
    CandidateSource,
    LegacyInventory,
    compare,
    read_witness,
    select,
    verify_candidate,
)
from openhands.sdk import Agent, LocalConversation
from openhands.sdk.agent.parallel_executor import ParallelToolExecutor
from openhands.sdk.event import (
    ActionEvent,
    Event,
    MessageEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.observability.trajectory_capture import (
    ShadowCapture,
    capture_run,
    use_admission,
)
from openhands.sdk.observability.trajectory_journal import Journal, encode
from openhands.sdk.tool.builtins.think import ThinkObservation


def _records(journal: Journal, root: Path):
    store = DirectoryStore(root)
    ref = seal_journal(journal, store, records_per_segment=2)
    return list(read_segments(store, read_seal(store, ref)))


def test_conversation_shadow_preserves_legacy_and_model_input(tmp_path, monkeypatch):
    ids = [uuid4(), uuid4()]
    root = tmp_path / "spool"
    monkeypatch.setenv("OPENHANDS_TRAJECTORY_SHADOW_SPOOL", str(root))
    monkeypatch.setenv("OPENHANDS_TRAJECTORY_SHADOW_IDS", str(ids[1]))
    wire = []

    def provider(**kwargs):
        wire.append(kwargs)
        return ModelResponse(
            id="same-provider-id",
            model="gpt-4o",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "done"}}],
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_completion", provider)
    agent = Agent(llm=LLM(model="gpt-4o", api_key=SecretStr("never-archive-this-key")))
    for cid in ids:
        conversation = LocalConversation(
            agent,
            workspace=str(tmp_path),
            conversation_id=cid,
            visualizer=None,
        )
        conversation.send_message("same question")
        conversation.run()
        assert any(
            isinstance(event, MessageEvent) and event.source == "agent"
            for event in conversation.state.events
        )
        conversation.close()

    assert sorted(path.stem for path in root.iterdir()) == [str(ids[1])]
    records = _records(Journal(root, str(ids[1])), tmp_path / "archive")
    (request,) = (item for item in records if item.record.kind == "attempt_start")
    assert request.model_input()["messages"] == wire[-1]["messages"]
    assert request.model_input()["tools"] == wire[-1]["tools"]
    assert "never-archive-this-key" not in str(records)
    report = coverage(iter(records))
    assert list(report.runs.values()) == ["finished"]
    assert set(report.model_responses.values()) == {"unavailable"}
    assert not report.missing_links


def test_retry_preserves_attempts_and_repeated_input(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "spool", "conversation")
    attempts = []

    def provider(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RateLimitError(
                message="try again", llm_provider="openai", model="gpt-4o"
            )
        return ModelResponse(
            id="model-id",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "done",
                        "reasoning_content": "why",
                    },
                }
            ],
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_completion", provider)
    question = Message(role="user", content=[TextContent(text="repeated")])
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("private-key"),
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
    )
    with capture_run(ShadowCapture(journal), lambda: None, lambda: "finished"):
        llm.completion(
            [question, question], extra_headers={"Authorization": "private-header"}
        )

    records = _records(journal, tmp_path / "archive")
    starts = [item for item in records if item.record.kind == "attempt_start"]
    assert len(starts) == 2
    first, second = starts
    assert first.record.data["request_id"] == second.record.data["request_id"]
    assert first.record.data["attempt_id"] != second.record.data["attempt_id"]
    assert second.model_input()["messages"] == attempts[1]["messages"]
    assert len(second.model_input()["messages"]) == 2
    assert first.record.data["payload_refs"] == second.record.data["payload_refs"]
    assert first.record.data["payload_refs"][1] == first.record.data["payload_refs"][2]
    assert "private-header" not in str(records)
    assert "private-key" not in str(records)
    report = coverage(iter(records))
    assert list(report.attempts.values()) == ["failed", "completed"]
    assert list(report.model_responses.values()) == ["available"]


@pytest.mark.asyncio
async def test_async_interruption_persists_stream_prefix(tmp_path, monkeypatch):
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

    journal = Journal(tmp_path / "spool", "conversation")
    arrived = asyncio.Event()

    async def stream():
        yield ModelResponseStream(
            id="stream",
            choices=[StreamingChoices(delta=Delta(content="prefix"), index=0)],
        )
        await asyncio.Future()

    async def provider(**_kwargs):
        return stream()

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_acompletion", provider)
    llm = LLM(model="gpt-4o", api_key=SecretStr("test"))
    question = Message(role="user", content=[TextContent(text="stream")])

    with capture_run(ShadowCapture(journal), lambda: None, lambda: "paused"):
        task = asyncio.create_task(
            llm.acompletion(
                [question], on_token=lambda _chunk: arrived.set(), stream=True
            )
        )
        await asyncio.wait_for(arrived.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    records = _records(journal, tmp_path / "archive")
    (fragment,) = (item for item in records if item.record.kind == "stream_fragment")
    assert fragment.payloads[0]["choices"][0]["delta"]["content"] == "prefix"
    assert coverage(iter(records)).attempts == {
        fragment.record.data["attempt_id"]: "aborted"
    }


def test_parallel_tool_results_match_native_ids_and_keep_recorded_order(
    tmp_path, monkeypatch
):
    journal = Journal(tmp_path / "spool", "conversation")
    capture = ShadowCapture(journal)
    provider_call_id = "reused-id"
    actions = [
        ActionEvent(
            id=f"custom-{index}",
            thought=[],
            tool_name="example",
            tool_call_id=provider_call_id,
            tool_call=MessageToolCall(
                id=provider_call_id, name="example", arguments="{}", origin="completion"
            ),
            llm_response_id="response",
            action=None,
            timestamp=f"2026-11-22T23:00:0{3 - index}",
        )
        for index in range(3)
    ]
    release = threading.Event()

    def run(action: ActionEvent) -> list[Event]:
        if action.id == "custom-0":
            assert release.wait(5)
        return [
            ObservationEvent(
                action_id=action.id,
                observation=ThinkObservation.from_text(action.id),
                tool_call_id=provider_call_id,
                tool_name="example",
            )
        ]

    with capture_run(capture, lambda: None, lambda: "finished"):
        for action in actions:
            capture.append_event(action, action_id=None)

        def release_first_when_second_is_stored(action_id, status, result, **kwargs):
            append_result(action_id, status, result, **kwargs)
            if action_id == "custom-1":
                release.set()

        append_result = capture.append_tool_result
        monkeypatch.setattr(
            capture, "append_tool_result", release_first_when_second_is_stored
        )
        ParallelToolExecutor(max_workers=2).execute_batch(actions[:2], run)
        capture.append_event(
            UserRejectObservation(
                tool_name="example",
                tool_call_id=provider_call_id,
                action_id=actions[2].id,
            ),
            action_id=actions[2].id,
        )

    records = _records(journal, tmp_path / "archive")
    assert [
        item.record.data["action_id"]
        for item in records
        if item.record.kind == "tool_result"
    ] == ["custom-1", "custom-0"]
    assert [
        item.record.data["event_id"]
        for item in records
        if item.record.data.get("event_kind") == "ActionEvent"
    ] == ["custom-0", "custom-1", "custom-2"]
    report = coverage(iter(records))
    assert report.actions == {
        "custom-0": ["success"],
        "custom-1": ["success"],
        "custom-2": ["rejected"],
    }


def test_sigkill_recovers_acknowledged_prefix(tmp_path):
    spool = tmp_path / "spool"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import sys, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from openhands.sdk.observability.trajectory_journal import Journal, encode
journal = Journal(Path(sys.argv[1]), "conversation")
journal.append("run_start", {}, run_id="run")
journal.append("attempt_start", {
    "adapter": "chat", "input_key": "messages", "input_count": 0,
    "parameters": {}, "attempt_id": "attempt", "request_id": "request"
}, run_id="run", payloads=(None,))
print("durable", flush=True)
time.sleep(60)
""",
            str(spool),
        ],
        env={**os.environ, "OPENHANDS_SUPPRESS_BANNER": "1"},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "durable"
    finally:
        child.kill()
        assert child.wait(timeout=5) == -signal.SIGKILL
    output = _records(Journal(spool, "conversation"), tmp_path / "archive")
    report = coverage(iter(output))
    assert report.runs == {"run": "unfinished"}
    assert report.attempts == {"attempt": "unfinished"}


def test_archive_retries_and_missing_objects(tmp_path):
    journal = Journal(tmp_path / "spool", "conversation")
    journal.append("run_start", {}, run_id="run")
    root = tmp_path / "archive"
    store = DirectoryStore(root)

    class LostAcknowledgment(DirectoryStore):
        def put(self, key, data):
            ref = super().put(key, data)
            raise OSError(f"Lost acknowledgment for {ref.key}")

    with pytest.raises(OSError, match="Lost acknowledgment"):
        seal_journal(journal, LostAcknowledgment(root))
    assert not list(root.rglob("*.json"))
    ref = seal_journal(journal, store)
    assert seal_journal(journal, store) == ref
    snapshot = read_seal(store, ref)
    segment = snapshot.segments[0]
    (root / segment.object.key).unlink()
    with pytest.raises(ArchiveGap, match="Unavailable object"):
        list(read_segments(store, snapshot))


def test_gcs_reuses_identical_object_after_lost_ack(tmp_path, monkeypatch):
    writes = {}
    missing = False
    bucket = Mock()

    def get_blob(name):
        if name in writes:
            return SimpleNamespace(generation=writes[name][0])
        return None

    def blob(name, *, generation=None):
        def upload(data, *, if_generation_match):
            assert if_generation_match == writes.get(name, (0, b""))[0]
            writes[name] = (len(writes) + 1, data)
            raise ServiceUnavailable("lost acknowledgment")

        def download(*, raw_download, if_generation_match):
            assert raw_download
            assert generation == if_generation_match == writes[name][0]
            if missing:
                raise NotFound("generation missing")
            return writes[name][1]

        return SimpleNamespace(
            upload_from_string=upload,
            download_as_bytes=download,
        )

    bucket.get_blob.side_effect = get_blob
    bucket.blob.side_effect = blob
    monkeypatch.setattr(storage, "Client", lambda: Mock(bucket=lambda _: bucket))
    store = GCSStore("gs://private/test")
    journal = Journal(tmp_path, "conversation")
    journal.append("run_start", {}, run_id="one")
    ref = seal_journal(journal, store)
    snapshot = read_seal(store, ref)
    assert snapshot.segments[0].object.generation == "1"
    journal.append("run_end", {"status": "finished"}, run_id="one")
    current = read_seal(store, seal_journal(journal, store))
    assert current.segments[0] == snapshot.segments[0]
    assert current.runs == {"one": "finished"}
    missing = True
    with pytest.raises(ArchiveGap, match="generation 1"):
        list(read_segments(store, snapshot))


def test_selection_keeps_verified_superset_and_exposes_conflicts(tmp_path):
    journal = Journal(tmp_path / "spool", "conversation")
    journal.append("run_start", {}, run_id="one")
    journal.append("run_end", {"status": "finished"}, run_id="one")
    records = _records(journal, tmp_path / "archive")
    shadow = coverage(iter(records))
    admission = Admission(
        conversation_id="conversation",
        origin="interactive",
        run_ids={"one", "lost"},
        evidence=shadow.evidence,
    )
    legacy = Candidate(
        conversation_id="conversation",
        source="legacy",
        archive=ObjectRef(key="legacy", generation="2", sha256="c" * 64, size=7),
        evidence={**shadow.evidence, "event:legacy": "a" * 64},
        runs={"one": "finished"},
    )
    current = legacy.model_copy(
        update={"source": "shadow", "evidence": shadow.evidence}
    )
    assert compare(admission, shadow, legacy)["missing_runs"] == ["lost"]
    assert select(admission, [current, legacy]) == (None, "missing_admitted_runs")
    admission = admission.model_copy(update={"run_ids": {"one"}})
    assert select(admission, [current, legacy]) == (legacy, "verified")
    conflict = current.model_copy(
        update={"evidence": {**shadow.evidence, "event:legacy": "b" * 64}}
    )
    assert select(admission, [legacy, conflict]) == (
        None,
        "conflicting_or_incomparable_evidence",
    )


def test_witness_reports_admission_and_survives_shadow_failure(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "shadow", "conversation")
    witness = Journal(tmp_path / "witness", "conversation")
    capture = ShadowCapture(journal, witness, origin="interactive")

    abandoned = capture.admit()
    ticket = capture.admit()
    assert abandoned is not None and ticket is not None

    def failed_write(kind, data, **kwargs):
        raise OSError("shadow disk unavailable")

    monkeypatch.setattr(journal, "append", failed_write)
    with use_admission(ticket), capture_run(capture, lambda: None, lambda: "finished"):
        pass

    expected = read_witness(witness, "interactive", None)
    records = _records(journal, tmp_path / "archive")
    result = compare(expected, coverage(iter(records)), None)
    assert result["missing_runs"] == sorted([abandoned.run_id, ticket.run_id])
    assert f"run_start:{ticket.run_id}" in result["stores"]["shadow"]["missing"]
    assert f"run_end:{ticket.run_id}" in result["stores"]["shadow"]["missing"]
    assert f"run_start:{abandoned.run_id}" not in expected.evidence


def test_seal_rejects_divergent_prefix_and_appends_only_tail(tmp_path):
    first = Journal(tmp_path / "first", "conversation")
    first.append("run_start", {}, run_id="run")
    store = DirectoryStore(tmp_path / "archive")
    old_ref = seal_journal(first, store)
    old = read_seal(store, old_ref)
    divergent = Journal(tmp_path / "other", "conversation")
    divergent.append("run_start", {}, run_id="different")
    divergent.append("run_end", {"status": "finished"}, run_id="different")
    with pytest.raises(ArchiveGap, match="disagrees with acknowledged"):
        seal_journal(divergent, store)
    first.append("run_end", {"status": "finished"}, run_id="run")
    current = read_seal(store, seal_journal(first, store))
    assert current.segments[0] == old.segments[0]
    assert current.segments[1].first == current.segments[1].last == 2
    assert read_seal(store, old_ref) == old


def test_candidate_requires_verified_native_bytes(tmp_path):
    store = DirectoryStore(tmp_path)
    location = str(tmp_path)
    body = {"id": "custom-id", "kind": "SystemPromptEvent", "system_prompt": "example"}
    wrapper = {
        "id": "custom-id",
        "kind": "UnparsedEvent",
        "original_kind": "SystemPromptEvent",
        "raw_payload": body,
    }
    event = store.put(
        "users/person/conversations/conversation/customid.json", encode(wrapper)
    )
    inventory = LegacyInventory(conversation_id="conversation", events=[event])
    ref = store.put("inventory.json", encode(inventory.model_dump()))
    source = CandidateSource(source="legacy", store=location, archive=ref)
    candidate = verify_candidate(store, source, "conversation")
    assert set(candidate.evidence) == {"event:custom-id"}
    assert not candidate.gaps
    admission = Admission(
        conversation_id="conversation",
        origin="unknown",
        run_ids=set(),
        evidence=candidate.evidence,
    )
    assert select(admission, [candidate]) == (candidate, "verified")
    (tmp_path / event.key).unlink()
    missing = verify_candidate(store, source, "conversation")
    assert missing.gaps and not missing.evidence
    assert select(admission, [missing]) == (None, "missing_verified_evidence")


def test_publish_recovers_interrupted_initialization_and_rejects_deleted_head(tmp_path):
    journal = Journal(tmp_path / "spool", "conversation")
    journal.append("run_start", {}, run_id="run")
    root = tmp_path / "archive"

    class InterruptedPublish(DirectoryStore):
        def put(self, key, data):
            if key.endswith("/published.json"):
                raise OSError("publisher stopped before acknowledgment")
            return super().put(key, data)

    with pytest.raises(OSError, match="before acknowledgment"):
        seal_journal(journal, InterruptedPublish(root))
    store = DirectoryStore(root)
    ref = seal_journal(journal, store)
    (root / "shadow/v1/conversation/head.json").unlink()
    with pytest.raises(ArchiveGap, match="acknowledged archive head is missing"):
        seal_journal(journal, store)
    assert read_seal(store, ref).through == 1


def test_recover_deleted_segment_even_when_gzip_output_changes(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "spool", "conversation")
    journal.append("run_start", {}, run_id="run")
    root = tmp_path / "archive"
    store = DirectoryStore(root)
    original = read_seal(store, seal_journal(journal, store))
    (segment,) = original.segments
    expected = list(read_segments(store, original))

    gzip_file = gzip.GzipFile

    def recompress(*args, **kwargs):
        return gzip_file(*args, **kwargs, compresslevel=1)

    monkeypatch.setattr(gzip, "GzipFile", recompress)
    (root / segment.object.key).unlink()
    recovered = read_seal(store, seal_journal(journal, store))
    (replacement,) = recovered.segments
    assert replacement.content_sha256 == segment.content_sha256
    assert replacement.object.sha256 != segment.object.sha256
    assert list(read_segments(store, recovered)) == expected
