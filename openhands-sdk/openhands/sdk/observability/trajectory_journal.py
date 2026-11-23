import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


SCHEMA_VERSION = 1
Origin = Literal["interactive", "automation", "evaluation", "unknown"]


def encode(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    sequence: int
    conversation_id: str
    run_id: str | None
    kind: str
    recorded_at: str
    data: dict[str, Any]


class Journal:
    def __init__(self, root: Path, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        if not conversation_id or Path(conversation_id).name != conversation_id:
            raise ValueError("Invalid journal conversation ID")
        if conversation_id == "..":
            raise ValueError("Invalid journal conversation ID")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / f"{conversation_id}.sqlite3"
        os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600))
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    sequence INTEGER PRIMARY KEY,
                    record_id TEXT UNIQUE NOT NULL,
                    body BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payloads (
                    hash TEXT PRIMARY KEY,
                    body BLOB NOT NULL,
                    first_sequence INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('instance', ?)", (uuid4().hex,)
            )
            self.instance_id: str = db.execute(
                "SELECT value FROM metadata WHERE key = 'instance'"
            ).fetchone()[0]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute("PRAGMA synchronous = FULL")
            yield db

    def append(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        run_id: str | None = None,
        record_id: str | None = None,
        payloads: tuple[Any, ...] = (),
    ) -> int:
        blobs = [encode(payload) for payload in payloads]
        hashes = [digest(blob) for blob in blobs]
        if hashes:
            data = {**data, "payload_refs": hashes}
        record_id = record_id if record_id is not None else uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.row_factory = sqlite3.Row
            existing = db.execute(
                "SELECT sequence, body FROM records WHERE record_id = ?", (record_id,)
            ).fetchone()
            if existing:
                record = Record.model_validate_json(existing["body"])
                if (record.kind, record.data) != (kind, data):
                    raise ValueError("Conflicting journal record")
                return record.sequence
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM records"
            ).fetchone()[0]
            record = Record(
                schema_version=SCHEMA_VERSION,
                sequence=sequence,
                conversation_id=self.conversation_id,
                run_id=run_id,
                kind=kind,
                recorded_at=datetime.now(UTC).isoformat(),
                data=data,
            )
            db.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (sequence, record_id, encode(record.model_dump())),
            )
            db.executemany(
                "INSERT OR IGNORE INTO payloads VALUES (?, ?, ?)",
                [
                    (key, blob, sequence)
                    for key, blob in zip(hashes, blobs, strict=True)
                ],
            )
        return sequence

    @contextmanager
    def read(
        self, *, after: int = 0
    ) -> Iterator[tuple[int, Iterator[tuple[Record, dict[str, Any]]]]]:
        with self._connect() as db:
            db.execute("BEGIN")
            head = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM records"
            ).fetchone()[0]
            rows = db.execute(
                "SELECT body FROM records WHERE sequence > ? ORDER BY sequence",
                (after,),
            )
            blobs = iter(
                db.execute(
                    "SELECT hash, body, first_sequence FROM payloads"
                    " WHERE first_sequence > ? ORDER BY first_sequence, hash",
                    (after,),
                )
            )

            def items() -> Iterator[tuple[Record, dict[str, Any]]]:
                blob = next(blobs, None)
                for (body,) in rows:
                    record = Record.model_validate_json(body)
                    payloads = {}
                    while blob and blob[2] == record.sequence:
                        key, raw, _ = blob
                        if digest(raw) != key:
                            raise ValueError("Corrupt journal payload")
                        payloads[key] = json.loads(raw)
                        blob = next(blobs, None)
                    yield record, payloads

            yield head, items()


def model_input(data: dict[str, Any], payloads: tuple[Any, ...]) -> dict[str, Any]:
    tools, *inputs = payloads
    count = data["input_count"]
    if count is not None and len(inputs) != count:
        raise ValueError("Missing ordered model input")
    return {
        **data["parameters"],
        "tools": tools,
        data["input_key"]: data["input_value"] if count is None else inputs,
    }


def boundary_evidence(
    kind: str, run_id: str | None, data: dict[str, Any], payloads: tuple[Any, ...]
) -> tuple[str, str] | None:
    values = {key: value for key, value in data.items() if key != "payload_refs"}
    match kind:
        case "run_start" | "run_end":
            key = run_id
        case "attempt_start":
            key = data["attempt_id"]
            values = {
                "run_id": run_id,
                "request_id": data["request_id"],
                "attempt": data.get("attempt", 1),
                "adapter": data["adapter"],
                "input": model_input(data, payloads),
                "unsupported_options": data.get("unsupported_options", []),
            }
        case "attempt_response":
            key = data["attempt_id"]
            values = payloads[0]
        case "attempt_end" | "stream_limit":
            key = data["attempt_id"]
        case "stream_fragment":
            key = f"{data['attempt_id']}:{data['index']}"
            values = payloads[0]
        case "request_end":
            key = data["request_id"]
        case "event" | "model_event" | "tool_result":
            key = data["event_id"]
            if kind == "event":
                values = payloads[0]
            elif kind == "tool_result":
                values = {**values, "value": payloads[0]}
        case "tool_start":
            key = data["execution_id"]
        case "capture_gap":
            key = data["gap_id"]
        case _:
            return None
    return f"{kind}:{key}", digest(encode(values))
