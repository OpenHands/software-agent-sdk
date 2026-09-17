from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from openhands.agent_server.conversation_service import (
    ConversationService,
    _append_system_message_suffix,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.persistence import (
    get_agent_profile_store,
    get_llm_profile_store,
)
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
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.secret import StaticSecret
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
_DISCOVER_PATH = "openhands.agent_server.agent_launch.discover_profile_skills"
_BROWSER_PROBE_PATH = "openhands.agent_server.agent_launch.is_tool_usable"
_LLM_PROFILE_REF = "default"


def _agent(suffix: str | None = None) -> Agent:
    context = AgentContext(system_message_suffix=suffix) if suffix else None
    return Agent(
        llm=LLM(model="gpt-4o", usage_id="llm"), tools=[], agent_context=context
    )


def _store_profile(suffix: str) -> OpenHandsAgentProfile:
    get_llm_profile_store().save(
        _LLM_PROFILE_REF,
        LLM(model="gpt-4o", usage_id="agent", api_key=SecretStr("llm-key")),
        include_secrets=True,
    )
    profile = OpenHandsAgentProfile(
        name="my-profile",
        revision=5,
        llm_profile_ref=_LLM_PROFILE_REF,
        system_message_suffix=suffix,
        tools=[],
    )
    get_agent_profile_store().save(profile)
    return profile


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
    additions = AgentLaunchAdditions(
        system_message_suffix_append=f"  {_RUNTIME_SERVICES}  ",
    )
    if profile_launch:
        profile = _store_profile("PROFILE_BASELINE")
        source: dict[str, Any] = {"agent_profile_id": profile.id}
    else:
        source = {"agent": _agent("PROFILE_BASELINE")}
    request = StartConversationRequest(
        **source,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        agent_launch_additions=additions,
        client_tools=[_CANVAS_UI],
    )
    captured: dict[str, Any] = {}
    service = ConversationService(conversations_dir=tmp_path)
    service._event_services = {}

    async def capture_start(stored, **kwargs):
        captured["stored"] = stored
        captured["agent"] = kwargs.get("agent")
        return _mock_event_service(
            ConversationState(
                id=uuid4(),
                agent=kwargs["agent"],
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
            )
        )

    with (
        patch(_DISCOVER_PATH, return_value=[]),
        patch(_BROWSER_PROBE_PATH, return_value=False),
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
        assert stored.launched_agent_profile is not None
        assert stored.launched_agent_profile.revision == 5

    restored_agent = type(agent).model_validate(agent.model_dump(mode="json"))
    assert restored_agent.agent_context is not None
    restored_suffix = restored_agent.agent_context.system_message_suffix
    assert restored_suffix is not None
    assert restored_suffix.count("<RUNTIME_SERVICES>") == 1
    assert [tool.name for tool in restored_agent.tools] == ["canvas_ui_client"]
    restored = StoredConversation.model_validate(stored.model_dump(mode="json"))
    assert restored.client_tools == [_CANVAS_UI]


@pytest.mark.asyncio
async def test_launch_additions_do_not_widen_a_profile_secret_scope(tmp_path):
    """Additions carry deployment context, never a wider scope than the profile."""
    profile = _store_profile("PROFILE_BASELINE").model_copy(update={"secret_refs": []})
    get_agent_profile_store().save(profile)
    request = StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        agent_launch_additions=AgentLaunchAdditions(
            system_message_suffix_append=_RUNTIME_SERVICES,
        ),
        secrets={"GITHUB_TOKEN": StaticSecret(value=SecretStr("gh"))},
    )
    captured: dict[str, Any] = {}
    service = ConversationService(conversations_dir=tmp_path)
    service._event_services = {}

    async def capture_start(stored, **kwargs):
        captured["stored"] = stored
        return _mock_event_service(
            ConversationState(
                id=uuid4(),
                agent=kwargs["agent"],
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
            )
        )

    with (
        patch(_DISCOVER_PATH, return_value=[]),
        patch(_BROWSER_PROBE_PATH, return_value=False),
        patch.object(
            service,
            "_start_event_service",
            new_callable=AsyncMock,
            side_effect=capture_start,
        ),
    ):
        await service.start_conversation(request)

    assert captured["stored"].secrets == {}
