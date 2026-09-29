"""Conversation catalog persistence is UTF-8 on disk; decode it as UTF-8.

``meta.json`` and ``base_state.json`` are written through
``openhands.sdk.utils.files.atomic_write_text`` (which hardcodes
``encoding="utf-8"``), but the catalog read paths in ``ConversationService``
and ``EventService.load_meta`` called ``Path.read_text()`` without an explicit
encoding. On a host whose default text encoding is a legacy ANSI codepage
(e.g. Windows cp936/cp1252), a conversation whose title or persisted state
contains non-ASCII text failed to decode; ``_load_catalog_sync`` logs and
continues, so the conversation silently disappeared from the catalog.

Run under ``PYTHONUTF8=0`` on a Windows host with a legacy ANSI codepage to
exercise the broken (pre-fix) path.
"""

import uuid

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StoredConversation
from openhands.sdk.workspace import LocalWorkspace


TITLE = "中文标题 🚀 keep-alive"


def _write_conversation_files(conversations_dir, conversation_id: uuid.UUID) -> None:
    """Persist catalog files with the exact bytes ``atomic_write_text`` writes."""
    conversation_dir = conversations_dir / conversation_id.hex
    conversation_dir.mkdir(parents=True)
    stored = StoredConversation(
        id=conversation_id,
        title=TITLE,
        workspace=LocalWorkspace(working_dir=str(conversations_dir)),
    )
    (conversation_dir / "meta.json").write_bytes(
        stored.model_dump_json().encode("utf-8")
    )
    base_state = b'{"execution_status": "idle", "note": "\xe4\xb8\xad\xf0\x9f\x9a\x80"}'
    (conversation_dir / "base_state.json").write_bytes(base_state)


@pytest.mark.asyncio
async def test_catalog_decodes_utf8_persistence_regardless_of_locale(tmp_path):
    conversation_id = uuid.uuid4()
    conversations_dir = tmp_path / "conversations"
    _write_conversation_files(conversations_dir, conversation_id)

    async with ConversationService(conversations_dir=conversations_dir) as service:
        record = service._conversation_records.get(conversation_id)

    assert record is not None, (
        "conversation written as UTF-8 was dropped from the catalog: "
        "read_text() decoded it with the platform locale"
    )
    assert record.stored.title == TITLE, (
        "stored title decoded with the wrong encoding (mojibake)"
    )
