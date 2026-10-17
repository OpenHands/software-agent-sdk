"""Conversation catalog persistence is UTF-8 on disk; decode it as UTF-8.

``meta.json`` and ``base_state.json`` are written through
``openhands.sdk.utils.files.atomic_write_text`` (which hardcodes
``encoding="utf-8"``), but the catalog read paths in ``ConversationService``,
``EventService.load_meta`` and ``EventService._scrub_persisted_credentials``
called ``Path.read_text()`` without an explicit encoding. On a host whose
default text encoding is a legacy ANSI codepage (e.g. Windows cp936/cp1252),
a conversation whose title or persisted state contains non-ASCII text failed
to decode; ``_load_catalog_sync`` logs and continues, so the conversation
silently disappeared from the catalog.

The tests force a non-UTF-8 default encoding in-process so they fail against
the pre-fix code even on a UTF-8 host (CI runs the server tests on Ubuntu,
where the default is UTF-8 and an unforced test would be green either way).
"""

import io
import uuid

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace


TITLE = "中文标题 🚀 keep-alive"


@pytest.fixture
def ascii_default_encoding(monkeypatch):
    """Make bare ``Path.read_text()`` decode as ASCII, reproducing a cp936 host.

    ``Path.read_text`` resolves its encoding through ``io.text_encoding(None)``
    (falling back to ``locale.getpreferredencoding(False)``), so patching that
    entry point drives the unpatched read paths into the decode failure. The
    process runs with ``sys.flags.utf8_mode == 1`` in several environments
    (PYTHONUTF8=1, or a UTF-8-aware launcher like ``uv run``), and in that mode
    ``io.text_encoding`` returns ``"utf-8"`` regardless of the locale -- which
    is exactly why a locale-only patch is not enough to fail the broken code.
    """
    real_text_encoding = io.text_encoding

    def _forced(encoding=None, stacklevel=1):
        if encoding is None:
            return "ascii"
        return real_text_encoding(encoding, stacklevel)

    monkeypatch.setattr(io, "text_encoding", _forced, raising=True)


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
    (conversation_dir / "base_state.json").write_bytes(
        _base_state_json(conversation_id).encode("utf-8")
    )


def _base_state_json(conversation_id: uuid.UUID) -> str:
    """A schema-valid ``ConversationState`` carrying non-ASCII text.

    The workspace path is the payload: it survives the scrub round-trip and is
    enough to make a locale-decoded read fail.
    """
    state = ConversationState(
        id=conversation_id,
        agent=Agent(llm=LLM(model="gpt-4o-mini", api_key="dummy")),
        workspace=LocalWorkspace(working_dir="/tmp/中文目录"),
    )
    return state.model_dump_json()


@pytest.mark.asyncio
async def test_catalog_decodes_utf8_persistence_regardless_of_locale(
    tmp_path, ascii_default_encoding
):
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


@pytest.mark.asyncio
async def test_scrub_persisted_credentials_decodes_base_state_as_utf8(
    tmp_path, ascii_default_encoding
):
    """``EventService._scrub_persisted_credentials`` reads base_state as UTF-8.

    This path is reached on resume and is *not* wrapped in a catch, so a bare
    ``read_text()`` here raises ``UnicodeDecodeError`` and fails the resume
    outright on a legacy-codepage host — unlike the catalog loader, which logs
    and skips.
    """
    conversation_id = uuid.uuid4()
    conversations_dir = tmp_path / "conversations"
    _write_conversation_files(conversations_dir, conversation_id)

    stored = StoredConversation(
        id=conversation_id,
        title=TITLE,
        workspace=LocalWorkspace(working_dir=str(conversations_dir)),
    )
    service = EventService(stored=stored, conversations_dir=conversations_dir)

    class _Binding:
        async def load(self):  # pragma: no cover - not reached
            raise AssertionError("load() is not part of the scrub path under test")

        async def replace(self, expected_version, value):  # pragma: no cover
            raise AssertionError("replace() is not part of the scrub path")

    # A binding must be present or the scrub returns before reading anything.
    await service._scrub_persisted_credentials({"SOME_SECRET": _Binding()})

    raw = (conversations_dir / conversation_id.hex / "base_state.json").read_text(
        encoding="utf-8"
    )
    assert "中" in raw, "the UTF-8 base_state must survive the scrub round-trip"
