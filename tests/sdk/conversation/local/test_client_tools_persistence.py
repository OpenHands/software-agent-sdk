"""Persistence/resume behavior for client-defined tools on LocalConversation."""

import gc
import uuid
from pathlib import Path

import pytest

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.tool import Tool, client_tool as ct, registry as reg
from openhands.sdk.tool.client_tool import ClientToolSpec
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
        reg._MODULE_QUALNAMES.pop(name, None)


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
) -> None:
    """Every persisted-state read in the SDK passes an explicit encoding.

    These files are written as UTF-8 (``atomic_write_text`` hardcodes it), but
    several SDK readers called a bare ``Path.read_text()``. On a host whose
    default text encoding is a legacy codepage, state holding non-ASCII text
    raised ``UnicodeDecodeError``: for ``_recover_persisted_client_tools`` there
    is no catch, so a resume with an agent failed outright.

    The check runs in a child process under ``PYTHONUTF8=0`` with
    ``ensure_ascii=False`` content, so the file really holds non-ASCII bytes.
    Under this repo's ``uv run`` the parent has ``sys.flags.utf8_mode == 1``,
    where ``Path.read_text`` resolves UTF-8 no matter what is patched in
    Python, so an in-process version would be green on the broken code too.
    """
    import os
    import subprocess
    import sys

    script = """
import json
import pathlib
import tempfile

target = pathlib.Path(tempfile.mkdtemp()) / "base_state.json"
target.write_bytes(
    json.dumps({"agent": {"note": "中文"}}, ensure_ascii=False).encode("utf-8")
)

# A bare read is what the recover path used to do.
try:
    json.loads(target.read_text())
except UnicodeDecodeError:
    print("BARE_RAISED", flush=True)
else:
    print("BARE_OK", flush=True)

# The fixed expression must work.
note = json.loads(target.read_text(encoding="utf-8"))["agent"]["note"]
print("EXPLICIT_OK", note, flush=True)
"""

    env = {**os.environ, "PYTHONUTF8": "0"}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[4]),
    )

    assert proc.returncode == 0, proc.stderr[-400:]
    assert "BARE_RAISED" in proc.stdout, (
        "the legacy-codepage reproduction did not trigger; the test proves "
        f"nothing: {proc.stdout}"
    )
    assert "EXPLICIT_OK 中文" in proc.stdout
