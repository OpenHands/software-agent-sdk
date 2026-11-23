import asyncio
from shutil import rmtree
from uuid import uuid4

import httpx
import pytest

from openhands.agent_server.persistence import trajectory_webhook
from openhands.agent_server.persistence.trajectory_archive import (
    ArchiveGap,
    DirectoryStore,
    ObjectRef,
    coverage,
    read_seal,
    read_segments,
)
from openhands.agent_server.persistence.trajectory_batches import (
    archive_prefix,
    archive_through,
    batch_after,
    commit_batch,
)
from openhands.agent_server.persistence.trajectory_validation import Admission, compare
from openhands.sdk.observability.trajectory_capture import (
    AdmissionTicket,
    ShadowCapture,
)
from openhands.sdk.observability.trajectory_journal import Journal


def test_acknowledged_batches_replay_through_the_existing_seal_reader(tmp_path):
    conversation, run = str(uuid4()), uuid4().hex
    journal = Journal(tmp_path / "spool", conversation)
    instance = journal.instance_id
    store = DirectoryStore(tmp_path / "archive")
    journal.append("run_start", {}, run_id=run, payloads=("shared",))
    first, through = batch_after(journal, 0)
    assert through == 1
    assert commit_batch(store, conversation, 1, first) == 1
    assert commit_batch(store, conversation, 1, first) == 1
    assert archive_through(store, conversation) == 1

    restarted = Journal(tmp_path / "spool", conversation)
    assert restarted.instance_id == instance
    assert Journal(tmp_path / "replacement", conversation).instance_id != instance
    journal.append("run_end", {"status": "finished"}, run_id=run, payloads=("shared",))
    second, through = batch_after(restarted, 1)
    assert through == 2
    with pytest.raises(ArchiveGap, match="sequence gap"):
        commit_batch(store, conversation, 3, second)
    assert commit_batch(store, conversation, 2, second) == 2
    with pytest.raises(ArchiveGap, match="conflicts"):
        commit_batch(store, conversation, 1, first.replace(b"shared", b"forged"))

    lost_run = uuid4().hex
    journal.append("run_start", {}, run_id=lost_run)
    rmtree(tmp_path / "spool")
    head = store.head(f"{archive_prefix(conversation)}/head.json")
    assert head is not None
    seal = read_seal(store, ObjectRef.model_validate_json(head.data))
    assert seal.runs == {run: "finished"}
    records = list(read_segments(store, seal))
    assert [record.payloads for record in records] == [("shared",), ("shared",)]
    evidence = coverage(iter(records))
    admission = Admission(
        conversation_id=conversation,
        origin="evaluation",
        run_ids={run, lost_run},
        externally_enumerated=True,
        evidence=evidence.evidence,
    )
    comparison = compare(admission, evidence, None)
    assert comparison["missing_runs"] == [lost_run]
    assert not comparison["shadow_eligible"]


@pytest.mark.asyncio
async def test_retries_after_the_run_finishes(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "spool", str(uuid4()))
    ticket = AdmissionTicket(ShadowCapture(journal), uuid4().hex)
    journal.append("run_end", {"status": "finished"}, run_id=ticket.run_id)
    done = asyncio.Event()
    done.set()
    replies = iter([429, 503, 200])
    delays = []
    uploads = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"acked_through": 0})
        uploads.append(request.content)
        status = next(replies)
        return httpx.Response(
            status,
            headers={"Retry-After": "3"},
            json={"acked_through": 1} if status == 200 else {},
        )

    retry_delay = trajectory_webhook._retry_delay

    def observe_delay(error, attempt):
        delays.append(retry_delay(error, attempt))
        return 0

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        trajectory_webhook.httpx, "AsyncClient", lambda **_kwargs: client
    )
    monkeypatch.setattr(trajectory_webhook, "_retry_delay", observe_delay)
    monkeypatch.setattr(trajectory_webhook, "_INTERVAL", 0)

    await asyncio.wait_for(
        trajectory_webhook.publish(ticket, "https://cloud.test", {}, done), 2
    )
    assert delays[0] == 3
    assert delays[1] >= 3
    assert len(uploads) == 3 and len(set(uploads)) == 1


@pytest.mark.asyncio
async def test_caught_up_upload_uses_only_local_reads(tmp_path):
    journal = Journal(tmp_path / "spool", str(uuid4()))
    journal.append("run_start", {}, run_id=uuid4().hex)
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(
            200, json={"acked_through": int(request.method == "POST")}
        )

    stream = trajectory_webhook._Stream("https://cloud.test/journal", journal)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await stream.flush(client, now=0)
        await stream.flush(client, now=1)
        await stream.flush(client, now=2)
    assert stream.caught_up
    assert requests == ["GET", "POST"]
