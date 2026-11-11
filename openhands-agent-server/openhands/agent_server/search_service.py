"""Content search service: FTS5 index lifecycle + HTTP-facing search.

The index is a derived artifact owned by this singleton. It syncs from the
conversation store on demand (mtime-aware, so each sync is cheap) and never
modifies conversations themselves. If the index file is deleted it is rebuilt
on the next sync.
"""

from __future__ import annotations

import threading
from pathlib import Path

from openhands.agent_server.config import Config
from openhands.agent_server.search_index import SearchIndex, backfill


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
                self._index = SearchIndex(self._index_path)
            return self._index

    def sync(self) -> dict:
        index = self._ensure_index()
        with self._lock:
            return backfill(index, self._config.conversations_path, incremental=True)

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
                "conversation_id": h.conversation_id,
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
