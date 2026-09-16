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
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Event,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
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


@pytest.mark.asyncio
async def test_crash_recovery_error_is_on_active_branch_for_next_turn(tmp_path):
    """A restart between event-file append and persisted HEAD must remain resumable.

    This reproduces the production ordering with real ConversationService,
    LocalConversation, EventLog, and filesystem persistence:

    1. Persist an action while the conversation is RUNNING.
    2. Simulate process death after the action file lands but before
       ``leaf_event_id`` is saved by rewinding only the persisted HEAD.
    3. Let a second service take over the expired lease and synthesize the
       crash-recovery AgentErrorEvent.
    4. Append a real follow-up user message, like the Iterate watchdog does.
    5. Build the view's manipulation indices, the same operation the
       summarizing condenser performs before the next LLM turn.

    The current implementation writes the recovery error as a sibling of the
    action (both parented to the stale HEAD), so the active branch contains a
    tool result without its tool call and manipulation_indices raises KeyError.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(_request(workspace_dir))
        assert primary._event_services is not None
        event_service = primary._event_services[conversation_info.id]
        state = await event_service.get_state()
        conversation = event_service._conversation
        assert conversation is not None
        conversation_dir = conversations_dir / conversation_info.id.hex

        # Establish a realistic active branch before the in-flight action.
        system_prompt = SystemPromptEvent(
            system_prompt=TextContent(text="You are a coding agent."), tools=[]
        )
        conversation._on_event(system_prompt)
        user_message = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="run the command")]
            ),
        )
        conversation._on_event(user_message)
        stale_leaf = state.leaf_event_id
        assert stale_leaf == user_message.id

        running_action = _create_running_terminal_action("chatcmpl-tool-crash-recovery")
        conversation._on_event(running_action)
        state.execution_status = ConversationExecutionStatus.RUNNING
        assert state.leaf_event_id == running_action.id

        # Reproduce the crash window: the action event file exists, while the
        # base snapshot still points at the preceding user message.
        base_state_path = conversation_dir / "base_state.json"
        base_state = json.loads(base_state_path.read_text())
        base_state["leaf_event_id"] = stale_leaf
        base_state_path.write_text(json.dumps(base_state))
        _expire_lease(conversation_dir)

        async with ConversationService(
            conversations_dir=conversations_dir
        ) as secondary:
            assert secondary._event_services is not None
            recovered = secondary._event_services[conversation_info.id]
            recovered_state = await recovered.get_state()

            disk_events = _load_disk_events(conversation_dir)
            recovery_error = next(
                event for event in disk_events if isinstance(event, AgentErrorEvent)
            )
            assert recovery_error.tool_call_id == running_action.tool_call_id

            # Resume exactly as a follow-up automation does.
            await recovered.send_message(
                Message(
                    role="user",
                    content=[TextContent(text="continue after the restart")],
                ),
                run=False,
            )

            # Must not raise: the next turn/condenser needs a valid tool-call
            # boundary in the active branch.
            recovered_state.view.manipulation_indices
