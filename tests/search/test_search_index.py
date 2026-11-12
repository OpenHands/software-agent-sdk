"""Tests for the conversation full-text search index."""

import uuid

import pytest

from openhands.agent_server import search_index as search_index_module
from openhands.agent_server.search_index import SearchIndex, backfill, mask_secrets
from tests.search.conftest import write_conversation


@pytest.fixture
def index(tmp_path):
    return SearchIndex(tmp_path / "search.db")


def test_backfill_and_content_search(index, conv_root):
    result = backfill(index, conv_root)
    assert result["conversations_seen"] == 9
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
        "SELECT COUNT(*) FROM messages_fts WHERE text LIKE '%redacted%'"
    ).fetchone()[0]
    assert masked == 1


def test_mask_secrets_patterns():
    assert mask_secrets("key ghp_1234567890abcdefghij here") == "key <redacted> here"
    assert "PRIVATE KEY" not in mask_secrets(
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    )
    # Shapes the SDK utilities already know (a sample of each family).
    assert "sk-or-v1-" not in mask_secrets("key sk-or-v1-abcdef0123456789abcdef here")
    assert "github_pat_" not in mask_secrets("key github_pat_ABCDEFGHIJ0123456789 here")
    assert "Bearer" not in mask_secrets("auth: Bearer abcdef0123456789abcdef01234")
    assert "SECRET" not in mask_secrets("url https://user:SECRET@example.com/x.git")
    assert "topsecret" not in mask_secrets("api_key='topsecret'")
    # The supplements the SDK utilities miss.
    assert "sk-ABCDEFGHIJKLMNOPQRSTUV" not in mask_secrets(
        "store sk-ABCDEFGHIJKLMNOPQRSTUV please"
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in mask_secrets("id AKIAIOSFODNN7EXAMPLE")


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
    assert index.stats()["conversations"] == 8


def test_reopen_index_preserves_data(tmp_path, conv_root):
    db = tmp_path / "search.db"
    first = SearchIndex(db)
    backfill(first, conv_root)
    first.close()
    second = SearchIndex(db)
    assert second.stats()["conversations"] == 9
    assert second.search("Rome"), "index must survive close/reopen"


def test_non_message_events_ignored(index, conv_root, tmp_path):
    backfill(index, conv_root)
    # An ActionEvent-style payload has no llm_message text and must be skipped.
    action = {"id": "act-1", "source": "agent", "action": {"command": "ls -la"}}
    assert index.index_event(index.search("Italy")[0].conversation_id, action) is False


# -- fuzzy search ------------------------------------------------------


def test_stemming_matches_plural_forms_both_ways(index, conv_root):
    backfill(index, conv_root)
    # Stored text is singular; query is plural (and vice versa).
    hits = index.search("alarmss")  # double-typo plural -> still finds "alarms"
    assert hits, "typo of a plural should match"
    assert {h.title for h in hits} == {"Garage door sensor"}
    # Query singular against stored plural ("alarms" stored in user message).
    assert index.search("alarm")


def test_typo_tolerance(index, conv_root):
    backfill(index, conv_root)
    # Single substitution on a 5+ char word.
    hits = index.search("grage sensor")
    assert hits and {h.title for h in hits} == {"Garage door sensor"}
    # Transposed letters ("doar" for "door") count as one edit.
    hits = index.search("doar sensor")
    assert hits and {h.title for h in hits} == {"Garage door sensor"}


def test_substring_fallback(index, conv_root):
    backfill(index, conv_root)
    # "arage" is not a prefix of any term and has no edit-1 match, so the
    # substring layer finds "garage".
    hits = index.search("arage alarms")
    assert hits and {h.title for h in hits} == {"Garage door sensor"}


def test_fuzzy_does_not_create_false_positives(index, conv_root):
    backfill(index, conv_root)
    assert index.search("kubernetees cluster") == []
    # A 3-letter token: below the typo/substring thresholds on purpose, so
    # short queries never fuzz into unrelated words.
    assert index.search("aaa") == []


def test_overmatch_guard_hub_not_highlighted(index, conv_root):
    """'huing' must find 'Hugging' but never light up unrelated 'Hub'.

    The porter stem of 'huing' is 'hu' — trusted as a prefix it would match
    every word starting with 'hu'. The guard drops the too-short stem and
    lets only real-word candidates (here: the subsequence match 'hugging')
    through.
    """
    backfill(index, conv_root)
    hits = index.search("huing")
    assert {h.title for h in hits} == {"ML platform"}
    snippet = hits[0].snippet
    assert "[Hugging]" in snippet
    assert "[Hub]" not in snippet


def test_overmatch_guard_door_not_highlighted_for_doing(index, conv_root):
    """'doing' (stem 'do') must not match 'door' via the stem prefix."""
    backfill(index, conv_root)
    hits = index.search("doing")
    assert hits, "'doing' is a real indexed word and must keep matching"
    assert {h.title for h in hits} == {"Household chores"}
    assert "[door]" not in hits[0].snippet
    # ...while a search for the word 'door' itself still finds it
    assert index.search("door")[0].title == "Garage door sensor"


def test_subsequence_fallback_recovers_skipped_letters(index, conv_root):
    """All letters present, in order, ≤2 skipped -> match.

    'huing' skips both g's of 'hugging' but keeps letter order; the
    substring layer can't see it (not contiguous), so the subsequence
    fallback must.
    """
    backfill(index, conv_root)
    hits = index.search("huing")
    assert {h.title for h in hits} == {"ML platform"}


def test_subsequence_fallback_gap_capped(index, conv_root):
    """Gap > 2 skipped letters -> no match (keeps the fallback honest)."""
    backfill(index, conv_root)
    # 'wobmt' vs 'wombat' would need 2 skipped letters but the corpus has no
    # wombat word; assert against a real corpus word instead: 'trasfrms' vs
    # 'transformers' skips 4 -> rejected.
    assert index.search("trasfrms") == []


def test_stem_truncated_token_known_word_still_matches(index, conv_root):
    """A correctly-typed word whose stem is short still matches itself.

    'doing' stems to 'do' but exists in the corpus, so the exact-word
    escape keeps it searchable; the too-short stem prefix is just not
    allowed to drag in 'door'.
    """
    backfill(index, conv_root)
    hits = index.search("doing laundry")
    assert {h.title for h in hits} == {"Household chores"}


def test_stem_truncated_unknown_token_returns_nothing(index, conv_root):
    """A stem-truncated token that is neither a real word nor any known
    word's typo must return no hits rather than every word sharing the
    truncated stem (which is what the prefix query would do)."""
    backfill(index, conv_root)
    # 'skipping' stems to 'skip' (truncated) and is not in the corpus, and
    # no corpus word is within its typo/substring distance; the guard
    # must drop the stem prefix instead of matching all 'skip*' words.
    assert index.search("skipping") == []


def test_punctuation_only_query_returns_nothing(index, conv_root):
    """No word tokens means nothing to match: the query degrades to no
    hits instead of an FTS syntax error."""
    backfill(index, conv_root)
    assert index.search("!!! ... ???") == []


def test_thinking_blocks_are_indexed(index, conv_root):
    """Anthropic extended-thinking text is searchable like normal text."""
    backfill(index, conv_root)
    hits = index.search("quokka enclosure")
    assert {h.title for h in hits} == {"Quokka photos"}


def test_action_event_reasoning_is_indexed(index, conv_root):
    """ActionEvents persist reasoning at the event top level
    (``reasoning_content``), not inside ``llm_message``; that text must be
    searchable too."""
    backfill(index, conv_root)
    hits = index.search("wombat burrow")
    assert {h.title for h in hits} == {"Wombat burrow notes"}


def test_plain_string_content_is_indexed(index, conv_root, tmp_path):
    """``Message.content`` may serialize to a plain string, not just content
    blocks; both shapes must end up searchable."""
    backfill(index, conv_root)
    hits = index.search("capybara")
    assert {h.title for h in hits} == {"Riverbank notes"}


def test_event_without_id_is_skipped(index, conv_root):
    """Events missing their id cannot own a stable index row; they are
    skipped without corrupting the rest of the conversation."""
    backfill(index, conv_root)
    # Fixture conversation 'Skip me' has one valid + one id-less event;
    # only the valid one is indexed, and the id-less one stays invisible.
    hits = index.search("llamas")
    assert {h.title for h in hits} == {"Skip me"}
    assert index.search("cannot be indexed") == []


def test_corrupt_json_files_are_skipped(conv_root, tmp_path):
    """meta.json/event files that fail to parse are skipped, not fatal —
    one unreadable file must not block indexing of everything else."""
    db = tmp_path / "search.db"
    # A conversation whose meta.json is broken.
    (conv_root / "not-a-uuid").mkdir(exist_ok=True)
    (conv_root / "not-a-uuid" / "meta.json").write_text("{not json")
    # A conversation with a valid meta.json but one broken event file.
    conv_id = write_conversation(
        conv_root,
        "Broken events",
        [("user", "the platypus is immune to venom")],
    )
    events_dir = conv_root / conv_id.replace("-", "") / "events"
    (events_dir / "event-00009-deadbeef.json").write_text("{broken")

    index = SearchIndex(db)
    backfill(index, conv_root)
    # The broken-meta conversation is skipped; the broken-event
    # conversation still indexes its valid event.
    assert index.search("platypus")
    assert not index.search("definitely-corrupt-marker")


def test_vocabulary_limit_degrades_to_prefix_only(conv_root, tmp_path, monkeypatch):
    """Above the vocab cap, fuzzy expansion switches to prefix-only
    matching instead of scanning a quarter-million words per search."""

    monkeypatch.setattr(search_index_module, "_VOCAB_FUZZY_LIMIT", 3)
    db = tmp_path / "search.db"
    index = SearchIndex(db)
    backfill(index, conv_root)
    # Prefix matching keeps working...
    assert index.search("gar")
    # ...but typo tolerance is off (vocab unknown), and it degrades
    # without raising.
    assert index.search("rume") == []
    assert index.search("arage") == []
    # A stem-truncated token with unknown vocab must degrade to nothing
    # rather than prefix-matching its truncated stem.
    assert index.search("skipping") == []


def test_prefix_expansion_capped(conv_root, tmp_path, monkeypatch):
    """A token prefixing many words keeps its expansion short: the raw
    prefix scan stops at 5 candidates."""

    monkeypatch.setattr(search_index_module, "_VOCAB_FUZZY_LIMIT", 10_000)
    db = tmp_path / "search.db"
    index = SearchIndex(db)
    write_conversation(
        conv_root,
        "Prefix family",
        [("user", " ".join(f"prefix{i:02d}word" for i in range(10)))],
    )
    backfill(index, conv_root)
    # Ten raw corpus words start with 'pref'; the prefix scan stops at 5.
    assert len(index._fuzzy_terms("pref")) == 5


def test_short_tokens_do_not_fuzz(index, conv_root):
    """3-letter tokens never fuzz (would match half the dictionary)."""
    backfill(index, conv_root)
    assert index.search("fce") == []
    assert index.search("hub")[0].title == "ML platform"


def test_multiconversation_ranking_and_limit(index, conv_root):
    backfill(index, conv_root)
    hits = index.search("italy")
    assert hits
    assert len(hits) <= 50
    # Multi-token AND: both words must be in the same row.
    hits = index.search("itinerary rome")
    assert hits and {h.title for h in hits} == {"Italy trip"}


def test_snippet_marks_matched_words(conv_root):
    """The snippet wraps matched words in [ ] so the UI can emphasize them."""
    idx = SearchIndex(conv_root.parent / "s.db")
    backfill(idx, conv_root)
    hits = idx.search("alarms")
    assert hits
    assert any("[alarms]" in h.snippet for h in hits)


def test_dropped_last_letter_still_matches(tmp_path, conv_root):
    """Porter stems "huggin" and "hugging" to different prefixes, so a bare
    prefix query misses; the raw prefix word must be quoted in instead."""
    idx = SearchIndex(conv_root.parent / "s.db")
    write_conversation(
        tmp_path / "convs2",
        "ML chat",
        [
            ("user", "We tried hugging face transformers and it worked."),
        ],
    )
    backfill(idx, tmp_path / "convs2")
    (hit,) = [h for h in idx.search("huggin") if h.title == "ML chat"]
    assert "hugging" in hit.snippet


def test_old_tokenizer_schema_is_rebuilt(tmp_path, conv_root):
    """An index built pre-porter must transparently rebuild on reopen."""

    db = tmp_path / "search.db"
    old = SearchIndex(db)
    # Simulate the pre-fuzzy schema by rebuilding the FTS table unicode61-only.
    old._conn.execute("DROP TABLE IF EXISTS messages_vocab")
    old._conn.execute("DROP TABLE messages_fts")
    old._conn.execute(
        "CREATE VIRTUAL TABLE messages_fts USING fts5("
        "text, conv_id UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
    )
    old._conn.commit()
    old.close()

    upgraded = SearchIndex(db)  # constructor detects and clears old tables
    # The tables exist but are empty: dir_mtime was cleared, so the next
    # backfill rescans everything.
    assert upgraded.stats()["conversations"] == 0
    assert backfill(upgraded, conv_root)["conversations_seen"] == 9
    # Stemmed query now works on the rebuilt index.
    assert upgraded.search("doors sensors")
    assert upgraded.search("italy rome")


def test_porter_index_missing_words_twin_is_rebuilt(tmp_path, conv_root):
    """Porter schema from before the fuzzy layer must rebuild too.

    An older fuzzy-capable build had the porter table but no raw-words
    twin; without this check the empty twin would never fill because the
    incremental backfill skips unchanged conversations.
    """
    db = tmp_path / "search.db"
    old = SearchIndex(db)
    backfill(old, conv_root)
    assert old.search("rome")  # index is live and populated
    old._conn.execute("DROP TABLE messages_words_vocab")
    old._conn.execute("DROP TABLE messages_words_fts")
    old._conn.commit()
    old.close()

    upgraded = SearchIndex(db)
    assert upgraded.stats()["conversations"] == 0  # cleared for full rebuild
    assert backfill(upgraded, conv_root)["conversations_seen"] == 9
    # Typos and substrings need the raw-word vocabulary, which only the
    # rebuild restores.
    assert upgraded.search("rume")  # typo for Rome
    assert {h.title for h in upgraded.search("arage")} == {"Garage door sensor"}


def test_non_dict_content_block_is_skipped(index, conv_root):
    """A malformed content block (not a dict) is skipped, not fatal: the
    rest of the message is still indexed."""
    write_conversation(
        conv_root,
        "Malformed block",
        [],
        events=[
            {
                "id": str(uuid.uuid4()),
                "timestamp": "2026-11-01T10:00:00",
                "source": "user",
                "llm_message": {
                    "role": "user",
                    "content": [
                        "bare strings are not blocks",
                        {"type": "text", "text": "valid block about axolotls"},
                    ],
                },
            }
        ],
    )
    backfill(index, conv_root)
    hits = index.search("axolotls")
    assert {h.title for h in hits} == {"Malformed block"}


def test_substring_fallback_matches_inside_words(index, conv_root):
    """When no word is within typo distance, a 5+ letter token still
    matches words containing it as a substring ("lophon" inside
    "xylophone")."""
    write_conversation(
        conv_root,
        "Substring target",
        [("user", "the band needs a xylophone player")],
    )
    backfill(index, conv_root)
    hits = index.search("lophon")
    assert {h.title for h in hits} == {"Substring target"}


def test_rowid_collision_steps_aside(index, conv_root):
    """Two events whose (conv_id, event_id) pair hashes to the same FTS
    rowid must both stay indexable: the loser steps to the next rowid."""
    import hashlib

    victim = "collision-victim"
    rowid = (
        int.from_bytes(hashlib.sha256(f"{victim}:any".encode()).digest()[:8], "big")
        & 0x7FFFFFFFFFFFFFFF
    )
    # A pre-existing row squats on the rowid the hash would assign.
    index._conn.execute(
        "INSERT INTO messages (event_id, conv_id, fts_rowid, source, ts) "
        "VALUES('squatter', ?, ?, 'user', NULL)",
        (victim, rowid),
    )
    assert index._fts_rowid_assign(victim, "any") != rowid


def test_dir_without_meta_json_is_skipped(index, conv_root):
    """A directory with no meta.json is not a conversation; the scan
    must skip it without counting or crashing."""
    (conv_root / "not-a-conversation").mkdir(exist_ok=True)
    (conv_root / "not-a-conversation" / "events").mkdir()
    stray_event = conv_root / "not-a-conversation" / "events" / "event-00000-x.json"
    stray_event.write_text("{}")
    stats = backfill(index, conv_root)
    assert stats["conversations_seen"] == 9
