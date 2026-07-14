from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openhands.agent_server.conversation_service import (
    ConversationService,
    _append_agent_tools,
    _append_system_message_suffix,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import LaunchedAgentProfile, StoredConversation
from openhands.sdk import LLM, Agent, AgentContext, Tool
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.request import (
    AgentLaunchAdditions,
    StartConversationRequest,
)
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.workspace import LocalWorkspace


_RUNTIME_SERVICES = """<RUNTIME_SERVICES>
* Automation: http://localhost:18001
</RUNTIME_SERVICES>"""


def _agent(suffix: str | None = None) -> Agent:
    context = AgentContext(system_message_suffix=suffix) if suffix else None
    return Agent(
        llm=LLM(model="gpt-4o", usage_id="llm"), tools=[], agent_context=context
    )


def _mock_event_service(state: ConversationState) -> AsyncMock:
    event_service = AsyncMock(spec=EventService)
    event_service.get_state.return_value = state
    event_service.stored = MagicMock(
        launched_agent_profile=None,
        client_tools=[],
        title=None,
        metrics=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        forked_from_conversation_id=None,
        forked_from_event_id=None,
    )
    return event_service


def test_launch_additions_are_additive_and_forbid_unknown_fields():
    request = StartConversationRequest(
        agent_profile_id=uuid4(),
        workspace=LocalWorkspace(working_dir="/tmp"),
        agent_launch_additions=AgentLaunchAdditions(
            system_message_suffix_append=_RUNTIME_SERVICES,
            tools_append=[Tool(name="canvas_ui")],
        ),
    )

    assert request.agent is None
    assert request.agent_launch_additions is not None
    assert (
        request.agent_launch_additions.system_message_suffix_append == _RUNTIME_SERVICES
    )
    assert request.agent_launch_additions.tools_append == [Tool(name="canvas_ui")]
    with pytest.raises(ValidationError, match="Extra inputs"):
        AgentLaunchAdditions.model_validate({"replacement": "unsafe"})


def test_launch_addition_uses_existing_acp_prompt_path():
    agent = ACPAgent(
        acp_command=["echo", "test"],
        agent_context=AgentContext(system_message_suffix="PROFILE_BASELINE"),
    )
    updated = _append_system_message_suffix(agent, _RUNTIME_SERVICES)

    assert updated.agent_context is not None
    suffix = updated.agent_context.to_acp_prompt_context()
    assert suffix is not None
    assert suffix.count("<RUNTIME_SERVICES>") == 1
    assert "PROFILE_BASELINE" in suffix


def test_launch_tools_append_without_changing_existing_resolution():
    agent = _agent().model_copy(update={"tools": [Tool(name="terminal")]})

    updated = _append_agent_tools(agent, [Tool(name="canvas_ui")])

    assert [tool.name for tool in updated.tools] == ["terminal", "canvas_ui"]
    assert _append_agent_tools(updated, [Tool(name="canvas_ui")]) is updated
    with pytest.raises(ValueError, match="conflicts with the resolved agent"):
        _append_agent_tools(updated, [Tool(name="canvas_ui", params={"v": 2})])


@pytest.mark.parametrize("profile_launch", [False, True])
@pytest.mark.asyncio
async def test_launch_additions_apply_after_agent_resolution(profile_launch, tmp_path):
    profile_id = uuid4()
    resolved_agent = _agent("PROFILE_BASELINE")
    launched = LaunchedAgentProfile(agent_profile_id=profile_id, revision=5)
    additions = AgentLaunchAdditions(
        system_message_suffix_append=f"  {_RUNTIME_SERVICES}  ",
        tools_append=[Tool(name="canvas_ui")],
    )
    request = (
        StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            agent_launch_additions=additions,
            tool_module_qualnames={"canvas_ui": "canvas_ui_tool"},
        )
        if profile_launch
        else StartConversationRequest(
            agent=resolved_agent,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            agent_launch_additions=additions,
            tool_module_qualnames={"canvas_ui": "canvas_ui_tool"},
        )
    )
    state = ConversationState(
        id=uuid4(),
        agent=resolved_agent,
        workspace=request.workspace,
        execution_status=ConversationExecutionStatus.IDLE,
    )
    captured: dict[str, Any] = {}
    service = ConversationService(conversations_dir=tmp_path)
    service._event_services = {}

    async def capture_start(stored):
        captured["stored"] = stored
        return _mock_event_service(state)

    with (
        patch(
            "openhands.agent_server.conversation_service._resolve_agent_from_profile",
            return_value=(resolved_agent, launched),
        ),
        patch.object(
            service,
            "_start_event_service",
            new_callable=AsyncMock,
            side_effect=capture_start,
        ),
        patch("importlib.import_module") as import_module,
    ):
        await service.start_conversation(request)

    stored = captured["stored"]
    assert stored.agent.agent_context is not None
    suffix = stored.agent.agent_context.system_message_suffix
    assert suffix == f"PROFILE_BASELINE\n\n{_RUNTIME_SERVICES}"
    assert [tool.name for tool in stored.agent.tools] == ["canvas_ui"]
    assert stored.agent_launch_additions is None
    assert stored.client_tools == []
    assert stored.tool_module_qualnames == {"canvas_ui": "canvas_ui_tool"}
    import_module.assert_called_once_with("canvas_ui_tool")

    restored = StoredConversation.model_validate(stored.model_dump(mode="json"))
    assert restored.agent.agent_context is not None
    restored_suffix = restored.agent.agent_context.system_message_suffix
    assert restored_suffix is not None
    assert restored_suffix.count("<RUNTIME_SERVICES>") == 1
    assert [tool.name for tool in restored.agent.tools] == ["canvas_ui"]
