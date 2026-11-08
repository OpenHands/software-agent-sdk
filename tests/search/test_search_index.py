"""Tests for the conversation full-text search index."""

import json
import uuid

import pytest

from openhands.agent_server.search_index import SearchIndex, backfill, mask_secrets


def _write_conversation(root, title, messages):
    conv_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, title))
    conv_dir = root / conv_id
    events_dir = conv_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "meta.json").write_text(
        json.dumps({"conversation_id": conv_id, "title": title})
    )
    for i, (source, text) in enumerate(messages):
        event = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{title}:{i}")),
            "timestamp": "2026-11-01T10:00:00",
            "source": source,
            "llm_message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }
        fname = f"event-{i:06d}-{event['id']}.json"
        (events_dir / fname).write_text(json.dumps(event))
    return conv_id


@pytest.fixture
def conv_root(tmp_path):
    convs = tmp_path / "conversations"
    convs.mkdir()
    _write_conversation(
        convs,
        "Garage door sensor",
        [
            ("user", "The garage door sensor keeps triggering false alarms."),
            ("agent", "False alarms happen below five degrees at night."),
        ],
    )
    _write_conversation(
        convs,
        "Italy trip",
        [("user", "Draft a ten day itinerary for Italy starting in Rome.")],
    )
    _write_conversation(
        convs,
        "Secret keeper",
        [("user", "Store this key: sk-ABCDEFGHIJKLMNOPQRSTUV please.")],
    )
    return convs


@pytest.fixture
def index(tmp_path):
    return SearchIndex(tmp_path / "search.db")


def test_backfill_and_content_search(index, conv_root):
    result = backfill(index, conv_root)
    assert result["conversations_seen"] == 3
    hits = index.search("garage false alarms")
    assert hits, "expected content match on garage conversation"
    assert {h.title for h in hits} == {"Garage door sensor"}


def test_title_search(index, conv_root):
    backfill(index, conv_root)
    hits = index.search("Italy")
    assert hits and hits[0].title == "Italy trip"
    assert hits[0].source == "title"


def test_prefix_matching(index, conv_root):
    backfill(index, conv_root)
    hits = index.search("itiner")
    assert hits and hits[0].title == "Italy trip"


def test_no_result_on_unrelated_query(index, conv_root):
    backfill(index, conv_root)
    assert index.search("kubernetes") == []


def test_backfill_idempotent(index, conv_root):
    backfill(index, conv_root)
    stats_after_first = index.stats()["messages"]
    second = backfill(index, conv_root)
    assert second["events_indexed"] == 0
    assert index.stats()["messages"] == stats_after_first


def test_secrets_masked_in_index(index, conv_root):
    backfill(index, conv_root)
    leak = index._conn.execute(
        "SELECT COUNT(*) FROM messages_fts WHERE text LIKE '%sk-ABC%'"
    ).fetchone()[0]
    assert leak == 0
    masked = index._conn.execute(
        "SELECT COUNT(*) FROM messages_fts WHERE text LIKE '%REDACTED%'"
    ).fetchone()[0]
    assert masked == 1


def test_mask_secrets_patterns():
    assert mask_secrets("key ghp_1234567890abcdefghij here") == "key [REDACTED] here"
    assert "PRIVATE KEY" not in mask_secrets(
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    )


def test_fts_injection_is_neutralized(index, conv_root):
    backfill(index, conv_root)
    # Operators, quotes and parens must not raise or alter semantics.
    assert index.search('"garage (door"') == index.search("garage door")
    assert index.search("NEAR(") == []


def test_remove_conversation(index, conv_root):
    backfill(index, conv_root)
    conv_id = index.search("Italy")[0].conversation_id
    index.remove_conversation(conv_id)
    assert index.search("Rome") == []
    assert index.stats()["conversations"] == 2


def test_reopen_index_preserves_data(tmp_path, conv_root):
    db = tmp_path / "search.db"
    first = SearchIndex(db)
    backfill(first, conv_root)
    first.close()
    second = SearchIndex(db)
    assert second.stats()["conversations"] == 3
    assert second.search("Rome"), "index must survive close/reopen"


def test_non_message_events_ignored(index, conv_root, tmp_path):
    backfill(index, conv_root)
    # An ActionEvent-style payload has no llm_message text and must be skipped.
    action = {"id": "act-1", "source": "agent", "action": {"command": "ls -la"}}
    assert index.index_event(index.search("Italy")[0].conversation_id, action) is False
