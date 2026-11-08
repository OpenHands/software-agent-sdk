"""Full-text search index for local conversation history.

Maintains a SQLite FTS5 index over conversation titles and MessageEvent text
so past conversations can be found by their content, not just title.

Design notes:
- The index is a derived, disposable artifact: deleting ``search-index.db``
  only degrades search (next backfill rebuilds it); conversations themselves
  are never touched.
- Only titles and MessageEvent text/thinking blocks are indexed. Tool
  observations and other event kinds are deliberately excluded (noise and
  secret-exposure risk).
- High-signal secret patterns are masked before text enters the index.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_EVENTS_DIR = "events"
_META_FILE = "meta.json"

# FTS5 query syntax is user input; quoting every token makes it a safe
# prefix query ("tok*") and neutralizes operators, quotes and parens.
def _to_fts_query(query: str) -> str:
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    if not tokens:
        return ""
    quoted = []
    for t in tokens:
        inner = t.replace('"', '""')
        quoted.append(f'"{inner}"*')
    return " AND ".join(quoted)


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]

_MASK = "[REDACTED]"


def mask_secrets(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(_MASK, text)
    return text


def _extract_text(event: dict) -> str | None:
    """Return indexable text from a MessageEvent payload, if any."""
    message = event.get("llm_message")
    if not isinstance(message, dict):
        return None
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") == "thinking" and isinstance(
                block.get("thinking"), str
            ):
                parts.append(block["thinking"])
    text = "\n".join(p for p in parts if p.strip())
    return text or None


@dataclass
class SearchHit:
    conversation_id: str
    title: str | None
    event_id: str
    source: str
    timestamp: str
    snippet: str
    rank: float


class SearchIndex:
    """SQLite FTS5 index over conversation titles and message text."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        conv_id TEXT PRIMARY KEY,
        title TEXT,
        last_event_ts TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        event_id TEXT PRIMARY KEY,
        conv_id TEXT NOT NULL,
        fts_rowid INTEGER NOT NULL UNIQUE,
        source TEXT NOT NULL,
        ts TEXT
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        text,
        conv_id UNINDEXED,
        tokenize = "unicode61 remove_diacritics 2"
    );
    CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id);
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(self._SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        self._conn.close()

    # -- writes ---------------------------------------------------------

    def _fts_rowid(self, conv_id: str, event_id: str) -> int:
        h = hashlib.sha256(f"{conv_id}:{event_id}".encode()).digest()
        return int.from_bytes(h[:8], "big") & 0x7FFFFFFFFFFFFFFF

    def _fts_rowid_assign(self, conv_id: str, event_id: str) -> int:
        """Reserve a stable, collision-free FTS rowid for (conv_id, event_id)."""
        rowid = self._fts_rowid(conv_id, event_id)
        while True:
            owner = self._conn.execute(
                "SELECT conv_id, event_id FROM messages WHERE fts_rowid=?",
                (rowid,),
            ).fetchone()
            if owner is None or owner == (conv_id, event_id):
                return rowid
            rowid += 1  # astronomically rare hash collision; just step aside

    def _fts_rowid_lookup(self, conv_id: str, event_id: str) -> int:
        return self._fts_rowid_assign(conv_id, event_id)

    def index_conversation(self, conv_id: str, title: str | None) -> None:
        self._conn.execute(
            "INSERT INTO conversations(conv_id, title) VALUES(?, ?) "
            "ON CONFLICT(conv_id) DO UPDATE SET title=excluded.title",
            (conv_id, title),
        )
        # Titles are searchable via a synthetic per-conversation "title" row.
        rowid = self._fts_rowid_assign(conv_id, "__title__")
        self._conn.execute(
            "INSERT OR IGNORE INTO messages(event_id, conv_id, fts_rowid, source, ts) "
            "VALUES(?,?,?,?,NULL)",
            (f"{conv_id}:__title__", conv_id, rowid, "title"),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO messages_fts(rowid, text, conv_id) VALUES(?,?,?)",
            (rowid, mask_secrets(title or ""), conv_id),
        )
        self._conn.commit()

    def index_event(self, conv_id: str, event: dict) -> bool:
        """Index one event payload. Returns True if newly indexed."""
        text = _extract_text(event)
        if not text:
            return False
        event_id = event.get("id")
        if not event_id:
            return False
        self._fts_rowid_assign(conv_id, event_id)
        rowid = self._fts_rowid_lookup(conv_id, event_id)
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO messages(event_id, conv_id, fts_rowid, source, ts) "
            "VALUES(?,?,?,?,?)",
            (
                event_id,
                conv_id,
                rowid,
                str(event.get("source", "unknown")),
                event.get("timestamp"),
            ),
        )
        self._conn.commit()
        if cur.rowcount == 0:  # already indexed
            return False
        self._conn.execute(
            "INSERT OR REPLACE INTO messages_fts(rowid, text, conv_id) VALUES(?,?,?)",
            (rowid, mask_secrets(text), conv_id),
        )

        ts = event.get("timestamp")
        if ts:
            self._conn.execute(
                "UPDATE conversations SET last_event_ts=? WHERE conv_id=? AND "
                "(last_event_ts IS NULL OR last_event_ts<?)",
                (ts, conv_id, ts),
            )
        self._conn.commit()
        return True

    def remove_conversation(self, conv_id: str) -> None:
        rows = self._conn.execute(
            "SELECT fts_rowid FROM messages WHERE conv_id=?", (conv_id,)
        ).fetchall()
        for (rowid,) in rows:
            self._conn.execute("DELETE FROM messages_fts WHERE rowid=?", (rowid,))
        self._conn.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        self._conn.execute("DELETE FROM conversations WHERE conv_id=?", (conv_id,))
        self._conn.commit()

    # -- reads ----------------------------------------------------------

    def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        fts_query = _to_fts_query(query)
        if not fts_query:
            return []
        rows = self._conn.execute(
            """
            SELECT f.conv_id, c.title, m.event_id, m.source, m.ts,
                   snippet(messages_fts, 0, '', '', '…', 12) AS snip,
                   rank
            FROM messages_fts f
            JOIN messages m ON m.fts_rowid = f.rowid
            LEFT JOIN conversations c ON c.conv_id = f.conv_id
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return [
            SearchHit(
                conversation_id=r[0],
                title=r[1],
                event_id=r[2],
                source=r[3],
                timestamp=r[4],
                snippet=r[5],
                rank=r[6],
            )
            for r in rows
        ]

    def stats(self) -> dict:
        convs = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        msgs = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"conversations": convs, "messages": msgs}


def backfill(index: SearchIndex, conversations_path: Path) -> dict:
    """Index every conversation found under ``conversations_path``.

    Idempotent: events already present are skipped, so re-running after new
    conversations appear only processes the delta.
    """
    indexed_events = 0
    conv_count = 0
    for conv_dir in sorted(conversations_path.glob(f"*/")):
        meta_file = conv_dir / _META_FILE
        if not meta_file.is_file():
            continue
        conv_id = conv_dir.name
        conv_count += 1
        try:
            meta = json.loads(meta_file.read_text())
        except json.JSONDecodeError:
            continue
        index.index_conversation(conv_id, meta.get("title"))
        events_dir = conv_dir / _EVENTS_DIR
        if not events_dir.is_dir():
            continue
        for event_file in sorted(events_dir.glob("event-*.json")):
            try:
                event = json.loads(event_file.read_text())
            except json.JSONDecodeError:
                continue
            if index.index_event(conv_id, event):
                indexed_events += 1
    return {"conversations_seen": conv_count, "events_indexed": indexed_events}
