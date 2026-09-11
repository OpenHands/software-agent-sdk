from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openhands.agent_server.conversation_service import (
    ConversationService,
    _append_system_message_suffix,
    _merge_launch_skills,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import LaunchedAgentProfile, StoredConversation
from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.request import (
    AgentLaunchAdditions,
    StartConversationRequest,
)
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.skills import Skill
from openhands.sdk.tool.client_tool import ClientToolSpec
from openhands.sdk.workspace import LocalWorkspace


_RUNTIME_SERVICES = """<RUNTIME_SERVICES>
* Automation: http://localhost:18001
</RUNTIME_SERVICES>"""
_CANVAS_UI = ClientToolSpec(
    name="canvas_ui_client",
    description="Control the Canvas UI.",
    parameters={"type": "object", "properties": {}},
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
        ),
    )

    assert request.agent is None
    assert request.agent_launch_additions is not None
    assert (
        request.agent_launch_additions.system_message_suffix_append == _RUNTIME_SERVICES
    )
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
    resolved_agent = _agent("PROFILE_BASELINE")
    launched = LaunchedAgentProfile(agent_profile_id=profile_id, revision=5)
    additions = AgentLaunchAdditions(
        system_message_suffix_append=f"  {_RUNTIME_SERVICES}  ",
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
    assert [tool.name for tool in restored_agent.tools] == ["canvas_ui_client"]
    restored = StoredConversation.model_validate(stored.model_dump(mode="json"))
    assert restored.client_tools == [_CANVAS_UI]


def _skill(name: str) -> Skill:
    return Skill(name=name, content=f"# {name}")


def _skill_names(agent: AgentBase) -> list[str]:
    context = agent.agent_context
    assert context is not None
    return sorted(skill.name for skill in context.skills)


def _agent_with(skills: list[Skill], disabled: list[str] | None = None) -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o", api_key="k", usage_id="agent"),
        tools=[],
        agent_context=AgentContext(skills=skills, disabled_skills=disabled or []),
    )


class TestMergeLaunchSkills:
    """Deployment-supplied skills for clients that ship their own catalog.

    An ``agent_profile_id`` launch sends no ``agent_settings``, so a client whose
    skills are bundled rather than server-discovered has no other channel
    (software-agent-sdk#3979).
    """

    def test_adds_skills_the_resolved_agent_lacks(self):
        merged = _merge_launch_skills(
            _agent_with([_skill("github")]), [_skill("docker")]
        )
        assert _skill_names(merged) == ["docker", "github"]

    def test_the_resolved_agent_wins_a_name_collision(self):
        # Its skills came from the profile and the server's own sources — the
        # more authoritative view — and AgentContext rejects duplicate names.
        agent = _agent_with([_skill("github")])
        merged = _merge_launch_skills(agent, [_skill("github")])
        assert _skill_names(merged) == ["github"]

    def test_the_profile_deny_list_still_wins(self):
        # An addition must not turn back on what the profile turned off.
        merged = _merge_launch_skills(
            _agent_with([], disabled=["docker"]),
            [_skill("docker"), _skill("code-review")],
        )
        assert _skill_names(merged) == ["code-review"]

    def test_no_additions_leaves_the_agent_untouched(self):
        agent = _agent_with([_skill("github")])
        assert _merge_launch_skills(agent, []) is agent

    def test_an_agent_without_a_context_still_receives_them(self):
        agent = Agent(llm=LLM(model="gpt-4o", api_key="k", usage_id="agent"), tools=[])
        merged = _merge_launch_skills(agent, [_skill("docker")])
        assert _skill_names(merged) == ["docker"]

    def test_the_field_defaults_to_none(self):
        # Every existing caller must keep sending no skills at all.
        assert AgentLaunchAdditions().skills is None
