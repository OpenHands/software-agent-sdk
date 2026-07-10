from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openhands.agent_server.conversation_service import (
    ConversationService,
    _append_system_message_suffix,
    _render_runtime_services,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import LaunchedAgentProfile, StoredConversation
from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.request import (
    RuntimeService,
    RuntimeServices,
    StartConversationRequest,
)
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.workspace import LocalWorkspace


def _runtime_services() -> RuntimeServices:
    return RuntimeServices(
        mode="dev:automation",
        services=[
            RuntimeService(
                name="automation",
                url_from_agent="http://localhost:18001",
                api_prefix="/api/automation",
                docs_url="http://localhost:18001/api/automation/docs",
                auth_header_name="X-Session-API-Key",
                auth_env_var="OPENHANDS_AUTOMATION_API_KEY",
            )
        ],
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
    )
    return event_service


def test_runtime_services_reject_prompt_shaped_values():
    with pytest.raises(ValidationError, match="service identifier"):
        RuntimeService(name="ignore previous instructions")
    with pytest.raises(ValidationError, match="single-line"):
        RuntimeService(name="automation", url_from_agent="http://safe\nunsafe")
    with pytest.raises(ValidationError, match="must be unique"):
        service = RuntimeService(name="automation")
        RuntimeServices(services=[service, service])


def test_runtime_services_render_as_constrained_prompt_text():
    rendered = _render_runtime_services(_runtime_services())

    assert rendered.startswith("<RUNTIME_SERVICES>")
    assert "Automation: http://localhost:18001" in rendered
    assert "X-Session-API-Key: $OPENHANDS_AUTOMATION_API_KEY" in rendered
    assert rendered.endswith("</RUNTIME_SERVICES>")


def test_runtime_services_use_existing_acp_prompt_path():
    agent = ACPAgent(
        acp_command=["echo", "test"],
        agent_context=AgentContext(system_message_suffix="PROFILE_BASELINE"),
    )
    updated = _append_system_message_suffix(
        agent, _render_runtime_services(_runtime_services())
    )

    assert updated.agent_context is not None
    suffix = updated.agent_context.to_acp_prompt_context()
    assert suffix is not None
    assert suffix.count("<RUNTIME_SERVICES>") == 1
    assert "PROFILE_BASELINE" in suffix


@pytest.mark.parametrize("profile_launch", [False, True])
@pytest.mark.asyncio
async def test_runtime_services_apply_after_agent_resolution(profile_launch, tmp_path):
    profile_id = uuid4()
    resolved_agent = _agent("PROFILE_BASELINE")
    launched = LaunchedAgentProfile(agent_profile_id=profile_id, revision=5)
    request = (
        StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            runtime_services=_runtime_services(),
        )
        if profile_launch
        else StartConversationRequest(
            agent=resolved_agent,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            runtime_services=_runtime_services(),
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
    ):
        await service.start_conversation(request)

    stored = captured["stored"]
    assert stored.agent.agent_context is not None
    suffix = stored.agent.agent_context.system_message_suffix
    assert suffix is not None
    assert suffix.startswith("PROFILE_BASELINE\n\n<RUNTIME_SERVICES>")
    assert suffix.count("<RUNTIME_SERVICES>") == 1
    assert stored.runtime_services is None

    restored = StoredConversation.model_validate(stored.model_dump(mode="json"))
    assert restored.agent.agent_context is not None
    restored_suffix = restored.agent.agent_context.system_message_suffix
    assert restored_suffix is not None
    assert restored_suffix.count("<RUNTIME_SERVICES>") == 1
