import gzip
import json
from typing import Any

from openhands.agent_server.persistence.trajectory_archive import (
    ArchiveGap,
    ObjectRef,
    ObjectStore,
    Seal,
    Segment,
    _check_record,
    _persist,
    read_seal,
    verified_bytes,
)
from openhands.sdk.observability.trajectory_journal import (
    Journal,
    Record,
    digest,
    encode,
)


MAX_BATCH_BYTES = 4 * 1024 * 1024
MAX_BATCH_RECORDS = 128


def batch_after(journal: Journal, after: int) -> tuple[bytes, int]:
    data = bytearray()
    through = after
    with journal.read(after=after) as (head, records):
        if after > head:
            raise ArchiveGap("Cloud acknowledged records missing from the spool")
        for record, payloads in records:
            row = encode({"record": record.model_dump(), "payloads": payloads}) + b"\n"
            if len(row) > MAX_BATCH_BYTES:
                if data:
                    break
                raise ArchiveGap("Trajectory record exceeds the transport limit")
            if len(data) + len(row) > MAX_BATCH_BYTES:
                break
            data.extend(row)
            through = record.sequence
            if through - after >= MAX_BATCH_RECORDS:
                break
    return bytes(data), through


def archive_prefix(conversation_id: str) -> str:
    return f"shadow/v1/{conversation_id}"


def archive_through(store: ObjectStore, conversation_id: str) -> int:
    prefix = archive_prefix(conversation_id)
    head = store.head(f"{prefix}/head.json")
    if head is None:
        if store.head(f"{prefix}/published.json") is not None:
            raise ArchiveGap("Previously acknowledged archive head is missing")
        return 0
    seal = read_seal(store, ObjectRef.model_validate_json(head.data))
    if seal.conversation_id != conversation_id:
        raise ArchiveGap("Archive head belongs to another conversation")
    marker_key = f"{prefix}/published.json"
    if store.head(marker_key) is None:
        _persist(store, marker_key, encode({"conversation_id": conversation_id}))
    return seal.through


def commit_batch(
    store: ObjectStore,
    conversation_id: str,
    first: int,
    data: bytes,
) -> int:
    if first < 1 or not data or len(data) > MAX_BATCH_BYTES or not data.endswith(b"\n"):
        raise ValueError("Invalid trajectory batch size or start")
    rows = data.splitlines()
    if len(rows) > MAX_BATCH_RECORDS:
        raise ValueError("Too many trajectory records")
    through = first + len(rows) - 1
    prefix = archive_prefix(conversation_id)
    head_key, marker_key = f"{prefix}/head.json", f"{prefix}/published.json"
    head = store.head(head_key)
    marker = store.head(marker_key)
    if head is None and marker is not None:
        raise ArchiveGap("Previously acknowledged archive head is missing")
    previous = (
        read_seal(store, ObjectRef.model_validate_json(head.data))
        if head
        else Seal(conversation_id=conversation_id, through=0, segments=[], runs={})
    )
    if previous.conversation_id != conversation_id:
        raise ArchiveGap("Archive head belongs to another conversation")

    checksum = digest(data)
    if first <= previous.through:
        for segment in previous.segments:
            if (
                segment.first == first
                and segment.last == through
                and segment.content_sha256 == checksum
            ):
                verified_bytes(store, segment.object)
                if marker is None:
                    _persist(
                        store, marker_key, encode({"conversation_id": conversation_id})
                    )
                return through
        raise ArchiveGap("Trajectory batch conflicts with the acknowledged prefix")
    if first != previous.through + 1:
        raise ArchiveGap("Trajectory batch leaves a sequence gap")

    runs = dict(previous.runs)
    for sequence, line in enumerate(rows, first):
        row: dict[str, Any] = json.loads(line)
        record = Record.model_validate(row["record"])
        _check_record(record, previous, sequence)
        payloads = row["payloads"]
        if not isinstance(payloads, dict) or any(
            digest(encode(payload)) != key for key, payload in payloads.items()
        ):
            raise ValueError("Invalid trajectory payload digest")
        if record.kind in {"run_start", "run_end"} and record.run_id is not None:
            runs[record.run_id] = record.data.get("status", "unfinished")

    compressed = gzip.compress(data, mtime=0)
    ref = _persist(
        store,
        f"{prefix}/segments/{first:012d}-{through:012d}-{digest(compressed)}.jsonl.gz",
        compressed,
    )
    segment = Segment(first=first, last=through, object=ref, content_sha256=checksum)
    seal = Seal(
        conversation_id=conversation_id,
        through=through,
        segments=[*previous.segments, segment],
        runs=runs,
    )
    sealed = encode(seal.model_dump())
    seal_ref = _persist(
        store, f"{prefix}/seals/{through:012d}-{digest(sealed)}.json", sealed
    )
    if head is None and store.head(marker_key) is not None:
        raise ArchiveGap("Archive initialization raced with an acknowledged publish")
    store.advance(head_key, head, encode(seal_ref.model_dump()))
    if marker is None:
        _persist(store, marker_key, encode({"conversation_id": conversation_id}))
    return through
