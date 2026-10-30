"""Persistence/resume behavior for client-defined tools on LocalConversation."""

import gc
import json
import uuid
from pathlib import Path

import pytest

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.settings import OpenHandsAgentSettings
from openhands.sdk.tool import Tool, client_tool as ct, registry as reg
from openhands.sdk.tool.client_tool import ClientToolSpec, register_client_tools
from openhands.sdk.utils.models import clear_subclass_cache


@pytest.fixture(autouse=True)
def _collect_dynamic_client_tool_classes():
    """Drop the dynamic ``ClientAction_*`` classes these tests define.

    Each client tool spec builds a new class, so a test that registers the same
    tool name twice leaves two same-named classes behind. They are only
    reachable through reference cycles, so until a cyclic collection runs they
    stay in ``Action.__subclasses__()`` and make the duplicate-class check in
    ``_get_checked_concrete_subclasses`` fail in *unrelated* later tests.
    Collecting on teardown, once the test frame is gone, keeps that leak local.
    """
    yield
    gc.collect()
    clear_subclass_cache()


def _make_agent() -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o", usage_id="test-llm"),
        tools=[Tool(name="terminal")],
    )


def _wipe_client_tool_globals(names: list[str]) -> None:
    """Simulate a fresh process where dynamic client tools are unregistered."""
    ct._client_action_types.clear()
    ct._client_action_schemas.clear()
    for name in names:
        reg._REG.pop(name, None)
        reg._USABILITY_REG.pop(name, None)
        reg._TOOL_CLASSES.pop(name, None)


def test_persisted_client_tools_resume_without_respecifying(tmp_path: Path) -> None:
    """A persisted conversation re-registers/injects client tools on resume.

    The caller does not pass ``client_tools`` on resume; the specs must be
    recovered from persisted state so the agent keeps the tools and resume does
    not fail with "tools were removed mid-conversation".
    """
    specs = [
        ClientToolSpec(
            name="persist_show_notification",
            description="show",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
            },
        ),
        ClientToolSpec(
            name="persist_navigate_to",
            description="nav",
            parameters={
                "type": "object",
                "properties": {"route": {"type": "string"}},
            },
        ),
    ]
    names = [s.name for s in specs]
    cid = uuid.uuid4()
    persist_dir = tmp_path / "persist"
    ws_dir = tmp_path / "ws"

    created = Conversation(
        agent=_make_agent(),
        workspace=str(ws_dir),
        persistence_dir=str(persist_dir),
        conversation_id=cid,
        client_tools=specs,
        delete_on_close=False,
    )
    assert set(names) <= {t.name for t in created.agent.tools}
    created.close()

    # Fresh-process simulation: drop the global registrations.
    _wipe_client_tool_globals(names)
    assert not any(n in reg.list_registered_tools() for n in names)

    # Resume WITHOUT re-supplying client_tools.
    resumed = Conversation(
        agent=_make_agent(),
        workspace=str(ws_dir),
        persistence_dir=str(persist_dir),
        conversation_id=cid,
        delete_on_close=False,
    )
    try:
        assert set(names) <= {t.name for t in resumed.agent.tools}
        # The dynamic action types are registered again so persisted
        # ClientAction_* events could be deserialized.
        for n in names:
            assert n in reg.list_registered_tools()
    finally:
        resumed.close()


def test_client_tool_named_like_an_unattached_builtin_resumes(
    tmp_path: Path,
) -> None:
    specs = [ClientToolSpec(name="route_task_to_model", description="client")]
    cid = uuid.uuid4()
    ws, persist = str(tmp_path / "ws"), str(tmp_path / "persist")
    Conversation(
        agent=_make_agent(),
        workspace=ws,
        persistence_dir=persist,
        conversation_id=cid,
        client_tools=specs,
        delete_on_close=False,
    ).close()
    _wipe_client_tool_globals(["route_task_to_model"])

    resumed = Conversation(
        agent=_make_agent(),
        workspace=ws,
        persistence_dir=persist,
        conversation_id=cid,
        delete_on_close=False,
    )
    try:
        assert "route_task_to_model" in {t.name for t in resumed.agent.tools}
    finally:
        resumed.close()


def test_client_tool_named_like_an_attached_builtin_is_rejected(
    tmp_path: Path,
) -> None:
    convo = Conversation(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(tmp_path / "persist"),
        conversation_id=uuid.uuid4(),
        client_tools=[ClientToolSpec(name="finish", description="client")],
        delete_on_close=False,
    )
    try:
        with pytest.raises(ValueError, match="Duplicate tool names"):
            convo._ensure_agent_ready()
    finally:
        convo.close()
        _wipe_client_tool_globals(["finish"])


def test_client_tool_named_like_a_builtin_leaves_other_agents_alone() -> None:
    register_client_tools([ClientToolSpec(name="switch_llm", description="client")])
    try:
        agent = OpenHandsAgentSettings(
            llm=LLM(model="gpt-4o", usage_id="test-llm")
        ).create_agent()
    finally:
        _wipe_client_tool_globals(["switch_llm"])

    assert "switch_llm" not in {t.name for t in agent.tools}
    assert "SwitchLLMTool" in agent.include_default_tools


def test_recover_persisted_client_tools_no_state(tmp_path: Path) -> None:
    """Recovery is a no-op (empty list) when there is no persisted base state."""
    convo = Conversation(
        agent=_make_agent(),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(tmp_path / "persist"),
        conversation_id=uuid.uuid4(),
        delete_on_close=False,
    )
    try:
        recovered = convo._recover_persisted_client_tools(
            str(tmp_path / "nonexistent"), uuid.uuid4()
        )
        assert recovered == []
    finally:
        convo.close()


def test_recover_persisted_client_tools_decodes_base_state_as_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_recover_persisted_client_tools`` reads base_state as UTF-8.

    base_state.json is written through ``atomic_write_text`` (which hardcodes
    ``encoding="utf-8"``), but this reader called a bare ``Path.read_text()``.
    On a host whose default text encoding is a legacy codepage (e.g. Windows
    cp936/cp1252), persisted state holding non-ASCII text raised
    ``UnicodeDecodeError``; the except clause only covers a missing or invalid
    JSON file, so a resume that recovers client tools failed outright.

    The default encoding is forced to ASCII in-process so the test is red
    against the bare read even on a UTF-8 host: ``Path.read_text`` resolves its
    encoding through ``io.text_encoding(None)``, which we patch here. A
    locale-only trick (``PYTHONUTF8=0`` in a child process) would not work --
    that flag does not turn a UTF-8 locale into a legacy codepage -- and under
    this repo's ``uv run`` the parent has ``sys.flags.utf8_mode == 1``, where
    ``io.text_encoding`` returns ``"utf-8"`` regardless of the locale.
    """
    import io

    real_text_encoding = io.text_encoding

    def _forced_ascii(encoding=None, stacklevel=1):
        if encoding is None:
            return "ascii"
        return real_text_encoding(encoding, stacklevel)

    monkeypatch.setattr(io, "text_encoding", _forced_ascii, raising=True)

    # The persisted state only needs to be valid JSON with non-ASCII text;
    # the reader under test decodes it before looking at any tool specs. The
    # conversation object carries a different ID so constructing it does not
    # try to resume the seeded state itself.
    cid = uuid.uuid4()
    state_dir = tmp_path / "persist" / cid.hex
    state_dir.mkdir(parents=True)
    (state_dir / "base_state.json").write_bytes(
        json.dumps({"agent": {"note": "中文", "tools": []}}, ensure_ascii=False).encode(
            "utf-8"
        )
    )

    convo = Conversation(
        agent=_make_agent(),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(tmp_path / "persist-other"),
        conversation_id=uuid.uuid4(),
        delete_on_close=False,
    )
    try:
        # On the bare read this raises UnicodeDecodeError (uncaught here, so
        # the resume path would fail); the explicit UTF-8 read recovers the
        # empty client-tool list from the non-ASCII state.
        recovered = convo._recover_persisted_client_tools(
            str(tmp_path / "persist"), cid
        )
        assert recovered == []
    finally:
        convo.close()
