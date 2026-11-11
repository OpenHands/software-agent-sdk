"""Tests for the conversation full-text search index."""

import pytest
from conftest import write_conversation

from openhands.agent_server.search_index import SearchIndex, backfill, mask_secrets


@pytest.fixture
def conv_root(tmp_path):
    convs = tmp_path / "conversations"
    convs.mkdir()
    write_conversation(
        convs,
        "Garage door sensor",
        [
            ("user", "The garage door sensor keeps triggering false alarms."),
            ("agent", "False alarms happen below five degrees at night."),
        ],
    )
    write_conversation(
        convs,
        "Italy trip",
        [("user", "Draft a ten day itinerary for Italy starting in Rome.")],
    )
    write_conversation(
        convs,
        "Secret keeper",
        [("user", "Store this key: sk-ABCDEFGHIJKLMNOPQRSTUV please.")],
    )
    write_conversation(
        convs,
        "ML platform",
        [("agent", "The Hugging Face Hub hosts transformers models.")],
    )
    write_conversation(
        convs,
        "Household chores",
        [("user", "I was doing laundry when the door bell rang.")],
    )
    return convs


@pytest.fixture
def index(tmp_path):
    return SearchIndex(tmp_path / "search.db")


def test_backfill_and_content_search(index, conv_root):
    result = backfill(index, conv_root)
    assert result["conversations_seen"] == 5
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
    assert index.stats()["conversations"] == 4


def test_reopen_index_preserves_data(tmp_path, conv_root):
    db = tmp_path / "search.db"
    first = SearchIndex(db)
    backfill(first, conv_root)
    first.close()
    second = SearchIndex(db)
    assert second.stats()["conversations"] == 5
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
    assert backfill(upgraded, conv_root)["conversations_seen"] == 5
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
    assert backfill(upgraded, conv_root)["conversations_seen"] == 5
    # Typos and substrings need the raw-word vocabulary, which only the
    # rebuild restores.
    assert upgraded.search("rume")  # typo for Rome
    assert {h.title for h in upgraded.search("arage")} == {"Garage door sensor"}
