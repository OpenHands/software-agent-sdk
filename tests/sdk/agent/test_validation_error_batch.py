"""Validation errors must not split the next request's tool-call batch."""

import asyncio
from collections import Counter

import pytest

from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, AgentErrorEvent, Event, ObservationEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("tool_concurrency_limit", [1, 3])
@pytest.mark.parametrize(
    "invalid_indices,error_kind,confirm",
    [
        ([0], "missing_argument", False),
        ([1], "missing_argument", False),
        ([2], "missing_argument", False),
        ([0, 2], "missing_argument", False),
        ([0, 1, 2], "missing_argument", False),
        ([1], "unknown_tool", False),
        ([1], "invalid_json", False),
        ([1], "missing_argument", True),
    ],
    ids=["first", "middle", "last", "multiple", "all", "unknown", "json", "confirm"],
)
async def test_validation_errors_preserve_response_batch(
    scripted_tool_batch: tuple[LocalConversation, Message, list[list[Message]]],
    asynchronous: bool,
    invalid_indices: list[int],
    error_kind: str,
    confirm: bool,
) -> None:
    conversation, response, requests = scripted_tool_batch
    assert response.tool_calls is not None
    call_ids = [call.id for call in response.tool_calls]
    for index in invalid_indices:
        call = response.tool_calls[index]
        if error_kind == "unknown_tool":
            call.name = "nonexistent_tool"
        else:
            call.arguments = "not json" if error_kind == "invalid_json" else "{}"
    if confirm:
        conversation.set_confirmation_policy(AlwaysConfirm())

    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()

    if confirm:
        assert conversation.state.execution_status == (
            ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        )
        assert len(requests) == 1
        events = list(conversation.state.events)
        assert [e.tool_call_id for e in events if isinstance(e, AgentErrorEvent)] == [
            call_ids[1]
        ]
        assert not any(isinstance(e, ObservationEvent) for e in events)
        conversation.set_confirmation_policy(NeverConfirm())
        if asynchronous:
            await conversation.arun()
        else:
            conversation.run()

    assert len(requests) == 2
    history = requests[1]
    batches = [message for message in history if message.tool_calls]
    assert len(batches) == 1
    batch = batches[0]
    assert batch.tool_calls is not None
    assert [call.id for call in batch.tool_calls] == call_ids
    assert batch.content == response.content
    assert batch.reasoning_content == response.reasoning_content
    assert batch.thinking_blocks == response.thinking_blocks
    assert batch.responses_reasoning_item == response.responses_reasoning_item
    results = [message for message in history if message.role == "tool"]
    assert Counter(result.tool_call_id for result in results) == Counter(call_ids)
    assert all(history.index(result) > history.index(batch) for result in results)
    for result in results:
        content = result.content[0]
        assert isinstance(content, TextContent)
        if result.tool_call_id not in {call_ids[i] for i in invalid_indices}:
            assert content.text == "Your thought has been logged."

    events = list(conversation.state.events)
    actions = [event for event in events if isinstance(event, ActionEvent)]
    assert [event.tool_call_id for event in actions if event.action is None] == [
        call_ids[i] for i in invalid_indices
    ]
    errors = [event for event in events if isinstance(event, AgentErrorEvent)]
    assert Counter(event.tool_call_id for event in errors) == Counter(
        call_ids[i] for i in invalid_indices
    )
    assert max(events.index(event) for event in actions) < min(
        events.index(event) for event in errors
    )


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("failure", ["callback", "cleanup", "cancel"])
async def test_dispatch_failure_flushes_prior_errors(
    scripted_tool_batch: tuple[LocalConversation, Message, list[list[Message]]],
    asynchronous: bool,
    failure: str,
) -> None:
    conversation, response, _ = scripted_tool_batch
    assert response.tool_calls is not None
    response.tool_calls[0].arguments = "{}"
    conversation._ensure_agent_ready()
    events: list[Event] = []
    original_error = (
        asyncio.CancelledError() if failure == "cancel" else RuntimeError("dispatch")
    )

    def emit(event: Event) -> None:
        events.append(event)
        if isinstance(event, ActionEvent) and event.tool_call_id == "call_1":
            raise original_error
        if isinstance(event, AgentErrorEvent) and failure == "cleanup":
            raise ValueError("cleanup callback failed")

    with pytest.raises(type(original_error)) as exc_info:
        if asynchronous:
            await conversation.agent.astep(conversation, on_event=emit)
        else:
            conversation.agent.step(conversation, on_event=emit)
    assert exc_info.value is original_error
    assert [
        event.tool_call_id for event in events if isinstance(event, ActionEvent)
    ] == [
        "call_0",
        "call_1",
    ]
    assert isinstance(events[-1], AgentErrorEvent)
    assert events[-1].tool_call_id == "call_0"
    assert not any(isinstance(event, ObservationEvent) for event in events)
