"""Integration-like tests for conversation lease ownership."""

import json
from pathlib import Path

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Function

from openhands.agent_server.conversation_lease import (
    LEASE_FILE_NAME,
    ConversationOwnershipLostError,
)
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, AgentErrorEvent, Event, ObservationEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation


def _request(workspace_dir: Path) -> StartConversationRequest:
    return StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )


def _create_running_terminal_action(tool_call_id: str = "call_1") -> ActionEvent:
    tool_call = MessageToolCall.from_chat_tool_call(
        ChatCompletionMessageToolCall(
            id=tool_call_id,
            type="function",
            function=Function(
                name="terminal",
                arguments='{"command": "sleep 30"}',
            ),
        )
    )
    return ActionEvent(
        thought=[TextContent(text="run sleep")],
        action=TerminalAction(command="sleep 30"),
        tool_name="terminal",
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id="response_1",
        security_risk=SecurityRisk.LOW,
        summary="run sleep",
    )


def _terminal_observation(action: ActionEvent, text: str = "done") -> ObservationEvent:
    return ObservationEvent(
        observation=TerminalObservation.from_text(
            text,
            command="sleep 30",
            exit_code=0,
        ),
        action_id=action.id,
        tool_name="terminal",
        tool_call_id=action.tool_call_id,
    )


def _load_disk_events(conversation_dir: Path) -> list[Event]:
    event_files = sorted(
        (conversation_dir / "events").glob("event-*.json"),
        key=lambda path: int(path.name.split("-")[1]),
    )
    return [Event.model_validate_json(path.read_text()) for path in event_files]


def _expire_lease(conversation_dir: Path) -> None:
    lease_path = conversation_dir / LEASE_FILE_NAME
    payload = json.loads(lease_path.read_text())
    payload["expires_at"] = 0
    lease_path.write_text(json.dumps(payload))


@pytest.mark.asyncio
async def test_live_lease_blocks_split_brain_resume_with_disk_events(tmp_path):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(_request(workspace_dir))
        assert primary._event_services is not None
        primary_event_service = primary._event_services[conversation_info.id]
        primary_state = await primary_event_service.get_state()
        conversation_dir = conversations_dir / conversation_info.id.hex

        running_action = _create_running_terminal_action()
        primary_state.events.append(running_action)
        primary_state.execution_status = ConversationExecutionStatus.RUNNING

        assert [
            type(event).__name__ for event in _load_disk_events(conversation_dir)
        ] == [
            "ActionEvent",
            "ConversationStateUpdateEvent",
        ]

        async with ConversationService(
            conversations_dir=conversations_dir
        ) as secondary:
            assert secondary._event_services is not None
            assert conversation_info.id not in secondary._event_services
            primary_state.events.append(_terminal_observation(running_action))

        disk_events = _load_disk_events(conversation_dir)
        assert any(isinstance(event, ObservationEvent) for event in disk_events)
        assert not any(isinstance(event, AgentErrorEvent) for event in disk_events)


@pytest.mark.asyncio
async def test_expired_lease_takeover_fences_stale_writer_with_disk_events(tmp_path):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(_request(workspace_dir))
        assert primary._event_services is not None
        primary_event_service = primary._event_services[conversation_info.id]
        primary_state = await primary_event_service.get_state()
        conversation_dir = conversations_dir / conversation_info.id.hex

        running_action = _create_running_terminal_action()
        primary_state.events.append(running_action)
        primary_state.execution_status = ConversationExecutionStatus.RUNNING
        _expire_lease(conversation_dir)

        async with ConversationService(
            conversations_dir=conversations_dir
        ) as secondary:
            assert secondary._event_services is not None
            assert conversation_info.id in secondary._event_services

            disk_events = _load_disk_events(conversation_dir)
            assert any(isinstance(event, AgentErrorEvent) for event in disk_events)

            with pytest.raises(ConversationOwnershipLostError):
                primary_state.events.append(
                    _terminal_observation(running_action, "late result")
                )

            with pytest.raises(ConversationOwnershipLostError):
                primary_state.execution_status = ConversationExecutionStatus.ERROR


def _assign_lease_to_foreign_host(conversation_dir: Path) -> None:
    """Rewrite the lease as held by an unverifiable owner on another host."""
    lease_path = conversation_dir / LEASE_FILE_NAME
    payload = json.loads(lease_path.read_text())
    payload["owner_host"] = "vanished-container-1234"
    lease_path.write_text(json.dumps(payload))


@pytest.mark.asyncio
async def test_leasing_disabled_server_rehydrates_despite_live_foreign_lease(tmp_path):
    """https://github.com/OpenHands/software-agent-sdk/issues/4192

    With the server default (leasing disabled), a restarted server rehydrates
    conversations even when the previous owner left a still-live lease that
    cannot be verified dead (e.g. a recreated container with a new hostname).
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(_request(workspace_dir))
        conversation_dir = conversations_dir / conversation_info.id.hex
        _assign_lease_to_foreign_host(conversation_dir)

        async with ConversationService(
            conversations_dir=conversations_dir,
            lease_ttl_seconds=0,
        ) as restarted:
            page = await restarted.search_conversations()
            assert conversation_info.id in [item.id for item in page.items]
            event_service = await restarted.get_event_service(conversation_info.id)
            assert event_service is not None
