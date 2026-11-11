"""Full-text search index over local conversation history.

SQLite FTS5 index over conversation titles and MessageEvent text so past
conversations can be found by content. The index is a derived, disposable
artifact: deleting ``search-index.db`` only degrades search until the next
backfill rebuilds it; conversations themselves are never modified.

Only titles and MessageEvent text/thinking blocks are indexed; tool
observations and other event kinds are excluded (noise and secret-exposure
risk). Known secret patterns are masked before text enters the index.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


_EVENTS_DIR = "events"
_META_FILE = "meta.json"

# Tokenizer mirror: the index's unicode61 tokenizer splits text on any
# non-alphanumeric character, so the query is split the same way. Pasted
# punctuation ("(door", "garage!") then never becomes part of a token, and
# every remaining token is still quoted below, neutralizing FTS syntax.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens(query: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(query) if t]


# FTS5 query syntax is user input; quoting every token makes it a safe
# prefix query ("tok*") and neutralizes operators, quotes and parens.
# The porter tokenizer stems both indexed text and MATCH queries, so
# plural/verb variants match in both directions without extra work.
def _quote(token: str) -> str:
    return f'"{token.replace(chr(34), chr(34) * 2)}"*'


def _quote_exact(token: str) -> str:
    """Quoted token without the prefix star: matches the stemmed word only."""
    return f'"{token.replace(chr(34), chr(34) * 2)}"'


def _is_subsequence(needle: str, hay: str) -> bool:
    """True when needle's letters appear in hay in the same order."""
    it = iter(hay)
    return all(ch in it for ch in needle)


def _levenshtein(a: str, b: str, cap: int = 2) -> int:
    """Damerau-Levenshtein distance, capped; returns cap+1 past the cap.

    Adjacent transpositions count as one edit ("wobmat" for "wombat").
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            d = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (
                prev2 is not None
                and i > 1
                and j > 1
                and ca == b[j - 2]
                and a[i - 2] == cb
            ):
                d = min(d, prev2[j - 2] + 1)
            cur.append(d)
            best = min(best, d)
        if best > cap:
            return cap + 1
        prev2 = prev
        prev = cur
    return prev[-1]


# Above this many distinct indexed words the in-memory vocabulary scan stops
# being "fast enough" on every search, so fuzzy expansion degrades to
# prefix-only (stemmed prefix queries still work; typos stop being corrected).
_VOCAB_FUZZY_LIMIT = 250_000


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S
    ),
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
        last_event_ts TEXT,
        dir_mtime REAL
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
        tokenize = "porter unicode61 remove_diacritics 2"
    );
    -- Raw-word twin of messages_fts, same rows, plain unicode61 tokenizer.
    -- Serves only as the fuzzy vocabulary: typo and substring discovery
    -- needs unstemmed words (see _fuzzy_terms).
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_words_fts USING fts5(
        text,
        conv_id UNINDEXED,
        tokenize = "unicode61 remove_diacritics 2"
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_words_vocab USING fts5vocab(
        "messages_words_fts", "row"
    );
    CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id);
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Shared across threads (request-thread pruning, event-loop sync):
        # every access is serialized by ContentSearchService's lock, so the
        # single connection stays safe.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # Fuzzy vocabulary, loaded lazily and cached until the next write:
        # a sorted term list plus terms bucketed by length for typo scans.
        self._vocab_cache: tuple[list[str], dict[int, list[str]]] | None = None
        # One-row in-memory fts5 table used to porter-stem single tokens.
        self._stem_probe: sqlite3.Connection | None = None
        self._rebuild_if_old_tokenizer()
        self._conn.executescript(self._SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")

    def _rebuild_if_old_tokenizer(self) -> None:
        """Drop pre-fuzzy indexes so the next backfill rebuilds them.

        An FTS table built before the porter tokenizer (or before the
        raw-words twin existed) cannot answer stemmed or fuzzy queries, so
        either mismatch triggers a full rebuild. Deleting the rows also
        clears ``dir_mtime``, which the incremental backfill uses to skip
        unchanged conversations.
        """
        fts_sql = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'messages_fts'"
        ).fetchone()
        if fts_sql is None:
            return  # fresh database
        words_sql = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'messages_words_fts'"
        ).fetchone()
        if fts_sql[0] is not None and "porter" in fts_sql[0] and words_sql is not None:
            return  # already current
        self._conn.executescript(
            """
            DROP TABLE IF EXISTS messages_vocab;
            DROP TABLE IF EXISTS messages_words_vocab;
            DROP TABLE IF EXISTS messages_words_fts;
            DROP TABLE IF EXISTS messages_fts;
            DELETE FROM messages;
            DELETE FROM conversations;
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- incremental sync bookkeeping -----------------------------------

    def conversation_mtime(self, conv_id: str) -> float | None:
        """Indexed conversation's directory mtime, or None if unseen."""
        row = self._conn.execute(
            "SELECT dir_mtime FROM conversations WHERE conv_id=?", (conv_id,)
        ).fetchone()
        return row[0] if row is not None else None

    def conversation_ids(self) -> set[str]:
        """IDs of all conversations currently indexed."""
        return {
            row[0]
            for row in self._conn.execute(
                "SELECT conv_id FROM conversations"
            ).fetchall()
        }

    def note_conversation_sync(self, conv_id: str, dir_mtime: float) -> None:
        """Record that a conversation was fully re-indexed at this mtime."""
        self._conn.execute(
            "UPDATE conversations SET dir_mtime=? WHERE conv_id=?", (dir_mtime, conv_id)
        )

    def commit(self) -> None:
        self._conn.commit()

    def _fuzzy_terms(self, token: str) -> list[str]:
        """Expand a user token into real indexed words.

        ``messages_words_fts`` holds the same text tokenized without
        stemming, so its vocabulary contains raw words. Typos are compared
        against raw words — porter stems are unreliable for them (``garage``
        stems to ``garag`` but its typo ``grage`` stems to itself) — and the
        discovered candidates are then quoted into the stemmed MATCH query.
        In order: raw words starting with the token (quoting matters:
        ``huggin`` prefixes raw ``hugging`` but their porter stems differ),
        typo matches (1-2 edits) for 4+ char tokens, substring matches for
        5+ char tokens.
        """
        if len(token) < 4:
            return []
        terms, buckets = self._vocab()
        if not terms:
            return []

        # Raw words starting with the token sit contiguously in the sorted list.
        i = bisect.bisect_left(terms, token)
        prefix_terms: list[str] = []
        while i < len(terms) and terms[i].startswith(token):
            if terms[i] != token:
                prefix_terms.append(terms[i])
            i += 1
            if len(prefix_terms) >= 5:
                break
        if prefix_terms:
            return prefix_terms

        max_typos = 1 if len(token) < 7 else 2
        fuzzy: list[str] = []
        for length in range(len(token) - max_typos, len(token) + max_typos + 1):
            for term in buckets.get(length, []):
                if _levenshtein(token, term, max_typos) <= max_typos:
                    fuzzy.append(term)
        if not fuzzy and len(token) >= 5:
            for term in terms:
                if token in term:
                    fuzzy.append(term)
        if not fuzzy and len(token) >= 5:
            # Typed-too-fast fallback: every letter present, in order, but
            # some letters were skipped ("huing" -> "hugging", "wobmt" ->
            # "wombat"). Gap is capped at 2 letters so this stays a last
            # resort, not a wildcard.
            for term in terms:
                if 0 <= len(term) - len(token) <= 2 and _is_subsequence(token, term):
                    fuzzy.append(term)
        return fuzzy[:5]

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

    def index_conversation(self, conv_id: str, title: str | None) -> None:
        self._vocab_cache = None
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
        self._conn.execute(
            "INSERT OR REPLACE INTO messages_words_fts(rowid, text, conv_id) "
            "VALUES(?,?,?)",
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
        rowid = self._fts_rowid_assign(conv_id, event_id)
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
        if cur.rowcount == 0:  # already indexed
            self._conn.commit()
            return False
        self._conn.execute(
            "INSERT OR REPLACE INTO messages_fts(rowid, text, conv_id) VALUES(?,?,?)",
            (rowid, mask_secrets(text), conv_id),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO messages_words_fts(rowid, text, conv_id) "
            "VALUES(?,?,?)",
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
        self._vocab_cache = None  # new words may have entered the vocabulary
        return True

    def remove_conversation(self, conv_id: str) -> None:
        self._vocab_cache = None
        rows = self._conn.execute(
            "SELECT fts_rowid FROM messages WHERE conv_id=?", (conv_id,)
        ).fetchall()
        for (rowid,) in rows:
            self._conn.execute("DELETE FROM messages_fts WHERE rowid=?", (rowid,))
            self._conn.execute("DELETE FROM messages_words_fts WHERE rowid=?", (rowid,))
        self._conn.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        self._conn.execute("DELETE FROM conversations WHERE conv_id=?", (conv_id,))
        self._conn.commit()

    # -- reads ----------------------------------------------------------

    def _build_fuzzy_fts_query(self, query: str) -> str:
        """Turn user input into a fuzzy FTS5 MATCH expression.

        Each token becomes a group of OR-ed quoted prefixes: the token itself
        plus its typo/substring expansions. Groups are AND-ed so every word
        the user typed must still appear, fuzzily, in a matching row.
        """
        tokens = _tokens(query)
        if not tokens:
            return ""
        groups = []
        for token in tokens:
            # Fuzzy expansion can return the token itself; re-quoting it as
            # a prefix would undo the truncated-stem guard below.
            candidates = [w for w in self._fuzzy_terms(token) if w != token]
            if self._stem_truncated(token):
                # The token's prefix would match every word sharing its
                # stem ("huing" -> "hu*" grabs "hub"); match only real
                # candidates, or the stemmed word itself when the token
                # is a known word.
                if self._vocab_contains(token):
                    group = [_quote_exact(token)] + [_quote(w) for w in candidates]
                elif candidates:
                    group = [_quote(w) for w in candidates]
                else:
                    return ""
            else:
                group = [_quote(token)] + [_quote(w) for w in candidates]
            groups.append("(" + " OR ".join(group) + ")")
        return " AND ".join(groups)

    def _vocab(self) -> tuple[list[str], dict[int, list[str]]]:
        """Sorted raw-word list + length buckets, or ([], {}) if too large."""
        if self._vocab_cache is None:
            terms = sorted(
                r[0]
                for r in self._conn.execute(
                    "SELECT term FROM messages_words_vocab"
                ).fetchall()
            )
            if len(terms) > _VOCAB_FUZZY_LIMIT:
                terms = []  # too big to scan per search; prefix queries remain
            buckets: dict[int, list[str]] = {}
            for term in terms:
                buckets.setdefault(len(term), []).append(term)
            self._vocab_cache = (terms, buckets)
        return self._vocab_cache

    def _vocab_contains(self, token: str) -> bool:
        """Exact raw-word membership. False when the vocabulary is unknown."""
        terms, _ = self._vocab()
        if not terms:
            return False
        i = bisect.bisect_left(terms, token)
        return i < len(terms) and terms[i] == token

    def _stem_truncated(self, token: str) -> bool:
        """True when the porter stemmer deletes a large chunk of the token.

        "Large" = 3+ letters and at least a third of the token; such stems
        match far too many unrelated words to be used as prefixes.
        """
        if len(token) < 5:
            return False
        stem = self._porter_stem(token)
        removed = len(token) - len(stem)
        return removed >= 3 and removed * 3 >= len(token)

    def _porter_stem(self, word: str) -> str:
        """Porter-stem one word via a one-row in-memory fts5 probe table.

        The probe reuses the same tokenizer settings as the main index, so
        the stem is exactly what the MATCH pipeline would produce.
        """
        if self._stem_probe is None:
            conn = sqlite3.connect(":memory:")
            conn.executescript(
                """
                CREATE VIRTUAL TABLE stem_probe_fts USING fts5(
                    word, tokenize = "porter unicode61 remove_diacritics 2"
                );
                CREATE VIRTUAL TABLE stem_probe_vocab USING fts5vocab(
                    "stem_probe_fts", "row"
                );
                """
            )
            self._stem_probe = conn
        conn = self._stem_probe
        conn.execute("DELETE FROM stem_probe_fts")
        conn.execute("INSERT INTO stem_probe_fts(word) VALUES (?)", (word,))
        row = conn.execute("SELECT term FROM stem_probe_vocab").fetchone()
        return row[0] if row else word

    def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        fts_query = self._build_fuzzy_fts_query(query)
        if not fts_query:
            return []
        rows = self._conn.execute(
            """
            SELECT f.conv_id, c.title, m.event_id, m.source, m.ts,
                   snippet(messages_fts, 0, '[', ']', '…', 12) AS snip,
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


def backfill(
    index: SearchIndex, conversations_path: Path, *, incremental: bool = False
) -> dict:
    """Index every conversation found under ``conversations_path``.

    Idempotent: events already present are skipped. With
    ``incremental=True``, conversations whose directory mtime is unchanged
    since the last run are skipped entirely.
    """
    indexed_events = 0
    conv_count = 0
    skipped = 0
    seen_ids: set[str] = set()
    for conv_dir in sorted(conversations_path.glob("*/")):
        meta_file = conv_dir / _META_FILE
        if not meta_file.is_file():
            continue
        conv_id = conv_dir.name
        seen_ids.add(conv_id)
        dir_mtime = conv_dir.stat().st_mtime
        if incremental:
            if index.conversation_mtime(conv_id) == dir_mtime:
                skipped += 1
                continue
        conv_count += 1
        try:
            meta = json.loads(meta_file.read_text())
        except json.JSONDecodeError:
            continue
        index.index_conversation(conv_id, meta.get("title"))
        index.note_conversation_sync(conv_id, dir_mtime)
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
    # Drop index entries whose conversation directory is gone: the delete
    # path prunes eagerly, but this also covers conversations deleted while
    # the server was down or before the search feature existed.
    pruned = 0
    for orphan in index.conversation_ids() - seen_ids:
        index.remove_conversation(orphan)
        pruned += 1
    index.commit()
    return {
        "conversations_seen": conv_count,
        "events_indexed": indexed_events,
        "skipped_unchanged": skipped,
        "pruned_orphans": pruned,
    }
