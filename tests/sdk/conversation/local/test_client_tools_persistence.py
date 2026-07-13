"""Persistence/resume behavior for client-defined tools on LocalConversation."""

import json
import uuid
from pathlib import Path

import pytest

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.tool import Tool, client_tool as ct, registry as reg
from openhands.sdk.tool.client_tool import (
    ClientToolRegistrationError,
    ClientToolSpec,
    extract_client_tool_specs,
)
from openhands.tools.terminal import TerminalTool


def _make_agent(tools: list[Tool] | None = None) -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o", usage_id="test-llm"),
        tools=tools if tools is not None else [Tool(name="terminal")],
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
    """Resume and migrate legacy persisted client tool specs."""
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

    base_state_path = persist_dir / cid.hex / BASE_STATE
    base_state = json.loads(base_state_path.read_text())
    for tool in base_state["agent"]["tools"]:
        if tool["name"] in names:
            tool["params"].pop("_openhands_client_tool")
    base_state_path.write_text(json.dumps(base_state))

    _wipe_client_tool_globals(names)
    assert not any(n in reg.list_registered_tools() for n in names)

    resumed = Conversation(
        agent=_make_agent(),
        workspace=str(ws_dir),
        persistence_dir=str(persist_dir),
        conversation_id=cid,
        delete_on_close=False,
    )
    try:
        assert set(names) <= {t.name for t in resumed.agent.tools}
        assert extract_client_tool_specs(resumed.agent.tools) == specs
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
        assert recovered == ([], set())
    finally:
        convo.close()


def test_client_tools_resume_from_file_store(tmp_path: Path) -> None:
    spec = ClientToolSpec(name="file_store_client", description="client")
    store = InMemoryFileStore()
    cid = uuid.uuid4()

    created = LocalConversation(
        agent=_make_agent(),
        workspace=str(tmp_path / "ws"),
        conversation_id=cid,
        client_tools=[spec],
        file_store=store,
        delete_on_close=False,
    )
    created.close()
    _wipe_client_tool_globals([spec.name])

    resumed = LocalConversation(
        agent=_make_agent(),
        workspace=str(tmp_path / "ws"),
        conversation_id=cid,
        file_store=store,
        delete_on_close=False,
    )
    try:
        assert extract_client_tool_specs(resumed.agent.tools) == [spec]
    finally:
        resumed.close()


def test_server_spec_param_is_not_migrated_as_client(tmp_path: Path) -> None:
    spec = ClientToolSpec(name="persisted_server_spec", description="server")
    server_tool = Tool(name=spec.name, params={"spec": spec.model_dump()})
    reg.register_tool(spec.name, TerminalTool)
    cid = uuid.uuid4()
    persist_dir = tmp_path / "persist"

    created = Conversation(
        agent=_make_agent([server_tool]),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(persist_dir),
        conversation_id=cid,
        delete_on_close=False,
    )
    created.close()

    resumed = Conversation(
        agent=_make_agent([server_tool]),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(persist_dir),
        conversation_id=cid,
        delete_on_close=False,
    )
    try:
        assert extract_client_tool_specs(resumed.agent.tools) == []
        assert resumed.agent.tools == [server_tool]
        assert reg.is_tool_registered_as(spec.name, TerminalTool)
    finally:
        resumed.close()


def test_marked_client_rejects_runtime_server_collision(tmp_path: Path) -> None:
    spec = ClientToolSpec(name="persisted_client_collision", description="client")
    cid = uuid.uuid4()
    persist_dir = tmp_path / "persist"

    created = Conversation(
        agent=_make_agent([]),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(persist_dir),
        conversation_id=cid,
        client_tools=[spec],
        delete_on_close=False,
    )
    created.close()

    server_tool = Tool(name=spec.name, params={"spec": spec.model_dump()})
    reg.register_tool(spec.name, TerminalTool)
    with pytest.raises(ClientToolRegistrationError, match="collides"):
        Conversation(
            agent=_make_agent([server_tool]),
            workspace=str(tmp_path / "ws"),
            persistence_dir=str(persist_dir),
            conversation_id=cid,
            delete_on_close=False,
        )
