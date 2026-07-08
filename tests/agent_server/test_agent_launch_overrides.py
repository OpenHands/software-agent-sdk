"""Tests for client-supplied launch-time agent overrides.

Covers:
- ``AgentLaunchOverrides`` request field validation / round-trip
- ``_apply_launch_overrides`` helper (append semantics, no-op cases)
- ``_start_conversation`` applying the suffix onto the ``agent_profile_id`` path
  (the enrichment channel that otherwise loses the client agent) and onto the
  direct ``agent`` path
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openhands.agent_server.conversation_service import (
    ConversationService,
    _apply_launch_overrides,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    AgentLaunchOverrides,
    LaunchedAgentProfile,
    StartConversationRequest,
)
from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.workspace import LocalWorkspace


_RUNTIME_SERVICES = "<RUNTIME_SERVICES>backend=/api/automation</RUNTIME_SERVICES>"


def _make_agent(suffix: str | None = None) -> Agent:
    context = AgentContext(system_message_suffix=suffix) if suffix else None
    return Agent(
        llm=LLM(model="gpt-4o", usage_id="llm"), tools=[], agent_context=context
    )


# ---------------------------------------------------------------------------
# Request-model field
# ---------------------------------------------------------------------------


class TestAgentLaunchOverridesField:
    def test_field_defaults_to_none(self):
        req = StartConversationRequest(
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        assert req.agent_launch_overrides is None

    def test_field_accepts_suffix_append(self):
        req = StartConversationRequest(
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            agent_launch_overrides=AgentLaunchOverrides(
                system_message_suffix_append=_RUNTIME_SERVICES
            ),
        )
        assert req.agent_launch_overrides is not None
        assert (
            req.agent_launch_overrides.system_message_suffix_append == _RUNTIME_SERVICES
        )

    def test_field_survives_model_dump(self):
        req = StartConversationRequest(
            agent_profile_id=uuid4(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            agent_launch_overrides=AgentLaunchOverrides(
                system_message_suffix_append=_RUNTIME_SERVICES
            ),
        )
        dumped = req.model_dump(mode="json")
        assert dumped["agent_launch_overrides"] == {
            "system_message_suffix_append": _RUNTIME_SERVICES
        }

    def test_overrides_allowed_alongside_agent_profile_id(self):
        """Overrides are additive and must NOT trip the mutual-exclusivity rule."""
        req = StartConversationRequest(
            agent_profile_id=uuid4(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            agent_launch_overrides=AgentLaunchOverrides(
                system_message_suffix_append=_RUNTIME_SERVICES
            ),
        )
        assert req.agent is None
        assert req.agent_profile_id is not None


# ---------------------------------------------------------------------------
# Helper: _apply_launch_overrides
# ---------------------------------------------------------------------------


class TestApplyLaunchOverrides:
    def test_none_overrides_is_noop(self):
        agent = _make_agent(suffix="EXISTING")
        result = _apply_launch_overrides(agent, None)
        assert result is agent

    def test_empty_suffix_is_noop(self):
        agent = _make_agent(suffix="EXISTING")
        result = _apply_launch_overrides(
            agent, AgentLaunchOverrides(system_message_suffix_append=None)
        )
        assert result is agent
        result = _apply_launch_overrides(
            agent, AgentLaunchOverrides(system_message_suffix_append="   ")
        )
        assert result is agent

    def test_append_onto_existing_suffix(self):
        agent = _make_agent(suffix="EXISTING")
        result = _apply_launch_overrides(
            agent,
            AgentLaunchOverrides(system_message_suffix_append=_RUNTIME_SERVICES),
        )
        assert result.agent_context is not None
        assert (
            result.agent_context.system_message_suffix
            == f"EXISTING\n\n{_RUNTIME_SERVICES}"
        )

    def test_append_when_no_existing_context(self):
        agent = _make_agent()
        assert agent.agent_context is None
        result = _apply_launch_overrides(
            agent,
            AgentLaunchOverrides(system_message_suffix_append=_RUNTIME_SERVICES),
        )
        assert result.agent_context is not None
        assert result.agent_context.system_message_suffix == _RUNTIME_SERVICES

    def test_does_not_touch_llm_or_tools(self):
        agent = _make_agent(suffix="EXISTING")
        result = _apply_launch_overrides(
            agent,
            AgentLaunchOverrides(system_message_suffix_append=_RUNTIME_SERVICES),
        )
        assert result.llm == agent.llm
        assert result.tools == agent.tools


# ---------------------------------------------------------------------------
# Service: _start_conversation applies overrides on both launch paths
# ---------------------------------------------------------------------------


def _mock_event_service(state: ConversationState) -> AsyncMock:
    mock_es = AsyncMock(spec=EventService)
    mock_es.get_state.return_value = state
    mock_es.stored = MagicMock(
        launched_agent_profile=None,
        client_tools=[],
        title=None,
        metrics=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        forked_from_conversation_id=None,
        forked_from_event_id=None,
    )
    return mock_es


class TestStartConversationAppliesOverrides:
    @pytest.mark.asyncio
    async def test_profile_path_carries_suffix_onto_resolved_agent(self, tmp_path):
        """The resolved profile agent must receive the client suffix append."""
        profile_id = uuid4()
        # Resolver returns a profile-built agent with its own baseline suffix.
        resolved_agent = _make_agent(suffix="PROFILE_BASELINE")
        launched = LaunchedAgentProfile(agent_profile_id=profile_id, revision=5)
        request = StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            agent_launch_overrides=AgentLaunchOverrides(
                system_message_suffix_append=_RUNTIME_SERVICES
            ),
        )

        captured: dict[str, Any] = {}
        mock_state = ConversationState(
            id=uuid4(),
            agent=resolved_agent,
            workspace=request.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
        )

        with patch(
            "openhands.agent_server.conversation_service._resolve_agent_from_profile",
            return_value=(resolved_agent, launched),
        ):
            service = ConversationService(conversations_dir=tmp_path)
            service._event_services = {}

            with patch.object(
                service, "_start_event_service", new_callable=AsyncMock
            ) as mock_ses:

                async def capture_start(stored):
                    captured["stored"] = stored
                    return _mock_event_service(mock_state)

                mock_ses.side_effect = capture_start
                await service.start_conversation(request)

        stored = captured.get("stored")
        assert stored is not None
        assert stored.agent is not None
        assert stored.agent.agent_context is not None
        assert stored.agent.agent_context.system_message_suffix == (
            f"PROFILE_BASELINE\n\n{_RUNTIME_SERVICES}"
        )
        # Overrides must not be persisted (already folded into the agent).
        assert "agent_launch_overrides" not in stored.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_agent_path_carries_suffix(self, tmp_path):
        request = StartConversationRequest(
            agent=_make_agent(suffix="CLIENT_BASELINE"),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            agent_launch_overrides=AgentLaunchOverrides(
                system_message_suffix_append=_RUNTIME_SERVICES
            ),
        )

        captured: dict[str, Any] = {}
        mock_state = ConversationState(
            id=uuid4(),
            agent=request.agent,
            workspace=request.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
        )

        service = ConversationService(conversations_dir=tmp_path)
        service._event_services = {}

        with patch.object(
            service, "_start_event_service", new_callable=AsyncMock
        ) as mock_ses:

            async def capture_start(stored):
                captured["stored"] = stored
                return _mock_event_service(mock_state)

            mock_ses.side_effect = capture_start
            await service.start_conversation(request)

        stored = captured.get("stored")
        assert stored is not None
        assert stored.agent is not None
        assert stored.agent.agent_context is not None
        assert stored.agent.agent_context.system_message_suffix == (
            f"CLIENT_BASELINE\n\n{_RUNTIME_SERVICES}"
        )

    @pytest.mark.asyncio
    async def test_no_overrides_leaves_agent_untouched(self, tmp_path):
        request = StartConversationRequest(
            agent=_make_agent(suffix="CLIENT_BASELINE"),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
        )

        captured: dict[str, Any] = {}
        mock_state = ConversationState(
            id=uuid4(),
            agent=request.agent,
            workspace=request.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
        )

        service = ConversationService(conversations_dir=tmp_path)
        service._event_services = {}

        with patch.object(
            service, "_start_event_service", new_callable=AsyncMock
        ) as mock_ses:

            async def capture_start(stored):
                captured["stored"] = stored
                return _mock_event_service(mock_state)

            mock_ses.side_effect = capture_start
            await service.start_conversation(request)

        stored = captured.get("stored")
        assert stored is not None
        assert stored.agent is not None
        assert stored.agent.agent_context is not None
        assert stored.agent.agent_context.system_message_suffix == "CLIENT_BASELINE"
