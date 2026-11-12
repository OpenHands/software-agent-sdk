"""Tests for the content search service layer."""

import shutil

from openhands.agent_server.config import Config
from openhands.agent_server.search_service import ContentSearchService
from tests.search.conftest import write_conversation


def _service(conv_root, tmp_path):
    config = Config(
        session_api_keys=["k"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "w",
    )
    return ContentSearchService(config)


def test_remove_conversation_accepts_dashed_id(conv_root, tmp_path):
    """The service layer normalizes the API's dashed id to the hex dir
    key the index uses (UUID.hex on disk)."""
    svc = _service(conv_root, tmp_path)
    conv_id = write_conversation(
        conv_root, "Dash delete me", [("user", "unique zebra marker text")]
    )
    assert svc.search("zebra")[0][0]  # indexed under the hex dir name
    # Mirror production: the directory is gone before the DELETE hook
    # prunes the index, or the next sync would re-index it.
    shutil.rmtree(conv_root / conv_id.replace("-", ""))
    # The dashed id the router passes must prune the hex-keyed rows.
    svc.remove_conversation(conv_id)
    assert svc.search("zebra")[0] == []


def test_search_hits_return_dashed_conversation_ids(conv_root, tmp_path):
    """Hits must carry the dashed UUID form used by the rest of the API
    (ConversationInfo.id), not the hex directory name."""
    svc = _service(conv_root, tmp_path)
    conv_id = write_conversation(
        conv_root, "Hex vs dash", [("user", "dashed id marker words")]
    )
    hits, _ = svc.search("dashed id marker")
    # Only this conversation carries the marker phrase.
    assert len(hits) == 1
    assert hits[0]["conversation_id"] == conv_id
    # and the raw index layer keeps the hex key
    assert conv_id.replace("-", "") in svc._index.conversation_ids()


def test_remove_before_index_is_a_no_op(conv_root, tmp_path):
    """The DELETE hook can fire before any search ever opened the index;
    removal must not crash or create one."""
    svc = _service(conv_root, tmp_path)
    svc.remove_conversation("0" * 32)
    assert svc._index is None  # no index was opened just to drop nothing


def test_corrupt_index_file_is_rebuilt(conv_root, tmp_path):
    """A corrupt search-index.db (crash mid-write, disk trouble) rebuilds
    instead of 500ing every search forever."""
    index_path = conv_root.parent / "search-index.db"
    index_path.write_bytes(b"this is not a sqlite database at all....")
    svc = _service(conv_root, tmp_path)
    hits, capped = svc.search("garage")
    assert hits, "search must work after the rebuild"
    assert index_path.exists()


def test_stats_reports_index_health(conv_root, tmp_path):
    """stats() exposes the underlying index counters once built."""
    svc = _service(conv_root, tmp_path)
    svc.search("garage")  # builds the index
    assert svc.stats()["conversations"] == 8
