from shutil import rmtree
from uuid import uuid4

import pytest

from openhands.agent_server.persistence.trajectory_archive import (
    ArchiveGap,
    DirectoryStore,
    ObjectRef,
    read_seal,
    read_segments,
)
from openhands.agent_server.persistence.trajectory_batches import (
    archive_prefix,
    archive_through,
    batch_after,
    commit_batch,
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

    rmtree(tmp_path / "spool")
    head = store.head(f"{archive_prefix(conversation)}/head.json")
    assert head is not None
    seal = read_seal(store, ObjectRef.model_validate_json(head.data))
    assert seal.runs == {run: "finished"}
    records = list(read_segments(store, seal))
    assert [record.payloads for record in records] == [("shared",), ("shared",)]
