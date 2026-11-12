"""Content search service: FTS5 index lifecycle + HTTP-facing search.

The index is a derived artifact owned by this singleton. It syncs from the
conversation store on demand (mtime-aware, so each sync is cheap) and never
modifies conversations themselves. If the index file is deleted it is rebuilt
on the next sync.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from uuid import UUID

from openhands.agent_server.config import Config
from openhands.agent_server.search_index import SearchIndex, backfill


logger = logging.getLogger(__name__)


def _canonical_conversation_id(conv_id: str) -> str:
    """Return the dashed UUID form of an indexed conversation id.

    Backfill keys conversations by their on-disk ``UUID.hex`` directory
    name, but the API exposes conversation ids as dashed UUIDs everywhere
    else (``ConversationInfo.id``); clients use hits' ids to open
    conversations, so search hits must carry the dashed form.
    """
    return str(UUID(conv_id)) if len(conv_id) == 32 else conv_id


class ContentSearchService:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._index: SearchIndex | None = None
        self._lock = threading.Lock()

    @property
    def _index_path(self) -> Path:
        return self._config.conversations_path.parent / "search-index.db"

    def _ensure_index(self) -> SearchIndex:
        with self._lock:
            if self._index is None:
                try:
                    self._index = SearchIndex(self._index_path)
                except sqlite3.DatabaseError:
                    # The index is a disposable derived artifact; a corrupt
                    # file (crash mid-write, disk trouble, old schema)
                    # rebuilds rather than 500ing every search forever.
                    logger.warning(
                        "Corrupt search index at %s; rebuilding", self._index_path
                    )
                    self._index_path.unlink(missing_ok=True)
                    # WAL companions can hold the same corruption.
                    for suffix in ("-wal", "-shm"):
                        self._index_path.with_name(
                            self._index_path.name + suffix
                        ).unlink(missing_ok=True)
                    self._index = SearchIndex(self._index_path)
            return self._index

    def sync(self) -> dict:
        index = self._ensure_index()
        with self._lock:
            return backfill(index, self._config.conversations_path, incremental=True)

    def remove_conversation(self, conv_id: str) -> None:
        """Drop a conversation from the index (no-op when never indexed).

        ``conv_id`` is the API's dashed conversation id; on disk, backfill
        indexes conversations under their undashed ``UUID.hex`` directory
        names, so the dashes are stripped before lookup.
        """
        if self._index is None:
            return
        with self._lock:
            self._index.remove_conversation(conv_id.replace("-", ""))

    def search(self, query: str, limit: int = 50) -> tuple[list[dict], bool]:
        """Return ``(hits, capped)``; ``capped`` is True only when results were
        actually truncated (one extra row is fetched to tell)."""
        if not query.strip():
            return [], False
        index = self._ensure_index()
        with self._lock:
            backfill(index, self._config.conversations_path, incremental=True)
            hits = index.search(query, limit + 1)
            capped = len(hits) > limit
        return [
            {
                "conversation_id": _canonical_conversation_id(h.conversation_id),
                "title": h.title,
                "event_id": h.event_id,
                "source": h.source,
                "timestamp": h.timestamp,
                "snippet": h.snippet,
            }
            for h in hits[:limit]
        ], capped

    def stats(self) -> dict:
        index = self._ensure_index()
        with self._lock:
            return index.stats()
