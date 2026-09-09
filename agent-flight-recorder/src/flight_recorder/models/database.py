"""Rebuildable SQLite query index."""

import sqlite3
from pathlib import Path

from flight_recorder.models.envelopes import Record


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
    trace_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    stop_reason TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    agent_span_id TEXT,
    span_type TEXT NOT NULL,
    status TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    kind TEXT NOT NULL,
    span_id TEXT,
    agent_span_id TEXT,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    record_json TEXT NOT NULL,
    UNIQUE(trace_id, sequence)
);
CREATE INDEX IF NOT EXISTS records_trace_kind ON records(trace_id, kind);
CREATE INDEX IF NOT EXISTS records_trace_span ON records(trace_id, span_id);
CREATE INDEX IF NOT EXISTS records_trace_agent ON records(trace_id, agent_span_id);
CREATE TABLE IF NOT EXISTS llm_calls (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    response_id TEXT,
    model TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    span_id TEXT,
    kind TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    detector_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_blobs (
    digest TEXT PRIMARY KEY,
    compression TEXT NOT NULL,
    media_type TEXT,
    byte_length INTEGER NOT NULL,
    content BLOB NOT NULL
);
"""


class TraceIndex:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def add_records(self, records: list[Record]) -> None:
        values = self._record_values(records)
        with self.connect() as connection:
            connection.executemany(
                """INSERT OR IGNORE INTO records
                (record_id, trace_id, sequence, kind, span_id, agent_span_id,
                 observed_at, payload_json, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )

    def replace_trace_records(self, trace_id: str, records: list[Record]) -> None:
        if any(record.trace_id != trace_id for record in records):
            raise ValueError("all records must belong to the rebuilt trace")
        values = self._record_values(records)
        with self.connect() as connection:
            for table in ("findings", "artifacts", "llm_calls", "spans", "runs"):
                connection.execute(
                    f"DELETE FROM {table} WHERE trace_id = ?", (trace_id,)
                )
            connection.execute("DELETE FROM records WHERE trace_id = ?", (trace_id,))
            connection.executemany(
                """INSERT INTO records
                (record_id, trace_id, sequence, kind, span_id, agent_span_id,
                 observed_at, payload_json, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )

    @staticmethod
    def _record_values(records: list[Record]) -> list[tuple[object, ...]]:
        values = []
        for record in records:
            if record.sequence is None:
                raise ValueError("committed records require a sequence")
            values.append(
                (
                    record.record_id,
                    record.trace_id,
                    record.sequence,
                    record.kind,
                    record.span_id,
                    record.agent_span_id,
                    record.observed_at.isoformat(),
                    record.model_dump_json(include={"payload"}),
                    record.model_dump_json(),
                )
            )
        return values

    def iter_records(self, trace_id: str, after_sequence: int = 0) -> list[Record]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM records "
                "WHERE trace_id = ? AND sequence > ? ORDER BY sequence",
                (trace_id, after_sequence),
            ).fetchall()
        return [Record.model_validate_json(row["record_json"]) for row in rows]

    def list_trace_ids(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT trace_id FROM records ORDER BY trace_id"
            ).fetchall()
        return [str(row["trace_id"]) for row in rows]
