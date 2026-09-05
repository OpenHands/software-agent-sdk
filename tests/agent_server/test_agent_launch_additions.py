from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openhands.agent_server.conversation_service import (
    ConversationService,
    _append_system_message_suffix,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import LaunchedAgentProfile, StoredConversation
from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.request import (
    AgentLaunchAdditions,
    StartConversationRequest,
)
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.tool.client_tool import ClientToolSpec
from openhands.sdk.workspace import LocalWorkspace


_RUNTIME_SERVICES = """<RUNTIME_SERVICES>
* Automation: http://localhost:18001
</RUNTIME_SERVICES>"""
_AUTOMATION_CONTENT = "# OpenHands Automations\n" + "Follow this procedure.\n" * 2_000
_CANVAS_UI = ClientToolSpec(
    name="canvas_ui_client",
    description="Control the Canvas UI.",
    parameters={"type": "object", "properties": {}},
)


def _automation_skill() -> Skill:
    return Skill(
        name="openhands-automation",
        content=_AUTOMATION_CONTENT,
        trigger=KeywordTrigger(keywords=["automation"]),
        description="Manage OpenHands automations.",
        is_agentskills_format=True,
        disable_model_invocation=True,
    )


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
        parent_conversation_id=None,
    )
    return event_service


def test_launch_additions_accept_context_and_forbid_unknown_fields():
    request = StartConversationRequest(
        agent_profile_id=uuid4(),
        workspace=LocalWorkspace(working_dir="/tmp"),
        agent_launch_additions=AgentLaunchAdditions(
            system_message_suffix_append=_RUNTIME_SERVICES,
            skills_append=[_automation_skill()],
        ),
    )

    assert request.agent is None
    assert request.agent_launch_additions is not None
    assert (
        request.agent_launch_additions.system_message_suffix_append == _RUNTIME_SERVICES
    )
    assert request.agent_launch_additions.skills_append == [_automation_skill()]
    assert len(request.agent_launch_additions.skills_append[0].content) > 32_768
    with pytest.raises(ValidationError, match="Extra inputs"):
        AgentLaunchAdditions.model_validate({"tools_append": []})


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


@pytest.mark.parametrize("profile_launch", [False, True])
@pytest.mark.asyncio
async def test_launch_additions_apply_after_agent_resolution(profile_launch, tmp_path):
    profile_id = uuid4()
    resolved_agent = _agent("PROFILE_BASELINE").model_copy(
        update={
            "agent_context": AgentContext(
                skills=[Skill(name="same", content="profile content")],
                disabled_skills=["blocked"],
                system_message_suffix="PROFILE_BASELINE",
            )
        }
    )
    launched = LaunchedAgentProfile(agent_profile_id=profile_id, revision=5)
    additions = AgentLaunchAdditions(
        system_message_suffix_append=f"  {_RUNTIME_SERVICES}  ",
        skills_append=[
            Skill(name="same", content="launch content"),
            Skill(name="blocked", content="blocked content"),
            Skill(name="added", content="added content"),
        ],
    )
    request = (
        StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            agent_launch_additions=additions,
            client_tools=[_CANVAS_UI],
        )
        if profile_launch
        else StartConversationRequest(
            agent=resolved_agent,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            agent_launch_additions=additions,
            client_tools=[_CANVAS_UI],
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

    async def capture_start(stored, **kwargs):
        captured["stored"] = stored
        captured["agent"] = kwargs.get("agent")
        return _mock_event_service(state)

    with (
        patch(
            "openhands.agent_server.conversation_service._resolve_agent_from_profile",
            return_value=(resolved_agent, launched),
        ) as resolve_profile,
        patch.object(
            service,
            "_start_event_service",
            new_callable=AsyncMock,
            side_effect=capture_start,
        ),
    ):
        await service.start_conversation(request)

    stored = captured["stored"]
    agent = captured["agent"]
    assert agent.agent_context is not None
    suffix = agent.agent_context.system_message_suffix
    assert suffix == f"PROFILE_BASELINE\n\n{_RUNTIME_SERVICES}"
    assert [(skill.name, skill.content) for skill in agent.agent_context.skills] == [
        ("same", "profile content"),
        ("added", "added content"),
    ]
    assert [tool.name for tool in agent.tools] == ["canvas_ui_client"]
    assert stored.agent_launch_additions is None
    assert stored.client_tools == [_CANVAS_UI]
    assert stored.tool_module_qualnames == {}
    if profile_launch:
        resolve_profile.assert_called_once()
    else:
        resolve_profile.assert_not_called()

    restored_agent = type(agent).model_validate(agent.model_dump(mode="json"))
    assert restored_agent.agent_context is not None
    restored_suffix = restored_agent.agent_context.system_message_suffix
    assert restored_suffix is not None
    assert restored_suffix.count("<RUNTIME_SERVICES>") == 1
    assert [skill.name for skill in restored_agent.agent_context.skills] == [
        "same",
        "added",
    ]
    assert [tool.name for tool in restored_agent.tools] == ["canvas_ui_client"]
    restored = StoredConversation.model_validate(stored.model_dump(mode="json"))
    assert restored.client_tools == [_CANVAS_UI]


@pytest.mark.asyncio
async def test_launch_skills_survive_native_acp_sourcing_and_trigger_later(
    tmp_path,
):
    profile_id = uuid4()
    profile_skill = Skill(
        name="profile-managed",
        content="profile managed content",
        trigger=KeywordTrigger(keywords=["profile"]),
    )
    resolved_agent = ACPAgent(
        acp_command=["echo", "test"],
        agent_context=AgentContext(
            skills=[profile_skill],
            system_message_suffix="PROFILE_BASELINE",
        ),
    )
    launched = LaunchedAgentProfile(agent_profile_id=profile_id, revision=3)
    request = StartConversationRequest(
        agent_profile_id=profile_id,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        agent_launch_additions=AgentLaunchAdditions(
            skills_append=[_automation_skill()]
        ),
    )
    settings_store = MagicMock()
    settings_store.load.return_value = None
    with (
        patch(
            "openhands.agent_server.conversation_service._resolve_agent_from_profile",
            return_value=(resolved_agent, launched),
        ),
        patch(
            "openhands.agent_server.persistence.get_settings_store",
            return_value=settings_store,
        ),
    ):
        async with ConversationService(
            conversations_dir=tmp_path / "conversations",
            acp_skill_sourcing="native",
        ) as service:
            info, created = await service.start_conversation(request)
            assert created is True
            event_service = await service.get_event_service(info.id)
            assert event_service is not None
            agent = event_service.get_conversation().agent
            assert isinstance(agent, ACPAgent)
            assert agent.agent_context is not None
            assert agent.agent_context.skills == [_automation_skill()]
            assert len(agent.agent_context.skills[0].content) > 32_768
            assert agent.agent_context.system_message_suffix == "PROFILE_BASELINE"
            assert event_service.stored.agent_launch_additions is None
            await event_service.send_message(
                Message(role="user", content=[TextContent(text="Hello")]),
                run=False,
            )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        acp_skill_sourcing="native",
    ) as restored_service:
        restored_event_service = await restored_service.get_event_service(info.id)
        assert restored_event_service is not None
        restored_agent = restored_event_service.get_conversation().agent
        assert isinstance(restored_agent, ACPAgent)
        assert restored_agent.agent_context is not None
        assert restored_agent.agent_context.skills == [_automation_skill()]
        assert restored_agent.agent_context.system_message_suffix == "PROFILE_BASELINE"

        state = await restored_event_service.get_state()
        messages = [event for event in state.events if isinstance(event, MessageEvent)]
        assert messages[0].extended_content == []

        await restored_event_service.send_message(
            Message(
                role="user",
                content=[TextContent(text="Update my automation schedule")],
            ),
            run=False,
        )
        state = await restored_event_service.get_state()
        messages = [event for event in state.events if isinstance(event, MessageEvent)]
        assert messages[1].activated_skills == ["openhands-automation"]
        assert _AUTOMATION_CONTENT in messages[1].extended_content[0].text
