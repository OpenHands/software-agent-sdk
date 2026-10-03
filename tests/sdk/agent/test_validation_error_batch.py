"""Validation errors must not split the next request's tool-call batch."""

import asyncio
import threading
from collections import Counter

import pytest

from openhands.sdk.conversation import ConversationCallbackType, LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, AgentErrorEvent, Event, ObservationEvent
from openhands.sdk.event.error_classification import FailureKind
from openhands.sdk.event.llm_convertible import UserRejectObservation
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.tool.builtins.think import (
    ThinkAction,
    ThinkExecutor,
    ThinkObservation,
)


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


@pytest.mark.parametrize("cancel", [False, True], ids=["error", "cancel"])
async def test_validation_callback_failure_preserves_conversation_outcome(
    scripted_tool_batch: tuple[LocalConversation, Message, list[list[Message]]],
    batch_callbacks: list[ConversationCallbackType],
    cancel: bool,
) -> None:
    conversation, response, _ = scripted_tool_batch
    assert response.tool_calls is not None
    response.tool_calls[0].arguments = "{}"
    failure = (
        asyncio.CancelledError("validation callback cancelled")
        if cancel
        else RuntimeError("validation callback failed")
    )
    received: list[ActionEvent | AgentErrorEvent] = []

    def callback(event: Event) -> None:
        if isinstance(event, (ActionEvent, AgentErrorEvent)):
            received.append(event)
        if isinstance(event, AgentErrorEvent):
            raise failure
        if isinstance(event, ActionEvent) and event.tool_call_id == "call_1":
            raise RuntimeError("later action callback failed")

    batch_callbacks.append(callback)
    if cancel:
        await conversation.arun()
        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
    else:
        with pytest.raises(ConversationRunError) as exc_info:
            await conversation.arun()
        assert exc_info.value.__cause__ is failure
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

    assert [type(event) for event in received] == [ActionEvent, AgentErrorEvent]
    assert all(event.tool_call_id == "call_0" for event in received)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("tool_concurrency_limit", [1, 3])
@pytest.mark.parametrize("execution_failure", ["value", "runtime", "observation"])
async def test_validation_and_execution_errors_share_one_batch(
    scripted_tool_batch: tuple[LocalConversation, Message, list[list[Message]]],
    monkeypatch: pytest.MonkeyPatch,
    asynchronous: bool,
    execution_failure: str,
) -> None:
    conversation, response, requests = scripted_tool_batch
    assert response.tool_calls is not None
    response.tool_calls[0].arguments = "{}"
    response.tool_calls[1].arguments = '{"thought": "fail"}'

    def execute(
        _self: ThinkExecutor,
        action: ThinkAction,
        _conversation: object = None,
    ) -> ThinkObservation:
        if action.thought == "fail":
            if execution_failure == "value":
                raise ValueError("execution failed")
            if execution_failure == "runtime":
                raise RuntimeError("execution failed")
            return ThinkObservation.from_text("execution failed", is_error=True)
        return ThinkObservation.from_text("succeeded")

    monkeypatch.setattr(ThinkExecutor, "__call__", execute)
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()

    history = requests[1]
    batches = [m for m in history if m.tool_calls]
    assert len(batches) == 1
    assert batches[0].tool_calls is not None
    assert [c.id for c in batches[0].tool_calls] == ["call_0", "call_1", "call_2"]
    results = [m for m in history if m.role == "tool"]
    assert [m.tool_call_id for m in results] == ["call_0", "call_1", "call_2"]
    assert "execution failed" in str(results[1].content)
    assert results[2].content == [TextContent(text="succeeded")]
    events = list(conversation.state.events)
    if execution_failure == "observation":
        result = next(
            e
            for e in events
            if isinstance(e, ObservationEvent) and e.tool_call_id == "call_1"
        )
        assert result.observation.is_error
    else:
        result = next(
            e
            for e in events
            if isinstance(e, AgentErrorEvent) and e.tool_call_id == "call_1"
        )
        assert result.classification is not None
        assert result.classification.kind == (
            FailureKind.INTERNAL
            if execution_failure == "runtime"
            else FailureKind.AGENT_ACTION
        )


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("reject", [False, True], ids=["approve", "reject"])
async def test_mixed_batch_confirmation_approval_and_rejection(
    scripted_tool_batch: tuple[LocalConversation, Message, list[list[Message]]],
    asynchronous: bool,
    reject: bool,
) -> None:
    conversation, response, requests = scripted_tool_batch
    assert response.tool_calls is not None
    response.tool_calls[1].arguments = "{}"
    conversation.set_confirmation_policy(AlwaysConfirm())
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()
    assert conversation.state.execution_status == (
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    if reject:
        conversation.reject_pending_actions("Rejected in test")
    else:
        conversation.set_confirmation_policy(NeverConfirm())
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()
    history = requests[1]
    assert len([m for m in history if m.tool_calls]) == 1
    assert Counter(m.tool_call_id for m in history if m.role == "tool") == Counter(
        ["call_0", "call_1", "call_2"]
    )
    events = list(conversation.state.events)
    assert len([e for e in events if isinstance(e, AgentErrorEvent)]) == 1
    result_type = UserRejectObservation if reject else ObservationEvent
    assert [e.tool_call_id for e in events if isinstance(e, result_type)] == [
        "call_0",
        "call_2",
    ]


@pytest.mark.parametrize("tool_concurrency_limit", [1, 3])
async def test_interrupt_during_execution_keeps_validation_result(
    scripted_tool_batch: tuple[LocalConversation, Message, list[list[Message]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation, response, requests = scripted_tool_batch
    assert response.tool_calls is not None
    response.tool_calls[0].arguments = "{}"
    started = threading.Event()
    stopped = threading.Event()

    def execute(
        _self: ThinkExecutor,
        _action: ThinkAction,
        _conversation: object = None,
    ) -> ThinkObservation:
        started.set()
        assert stopped.wait(timeout=5), "executor was not interrupted"
        return ThinkObservation.from_text("stopped")

    def interrupt(_self: ThinkExecutor) -> None:
        stopped.set()

    monkeypatch.setattr(ThinkExecutor, "__call__", execute)
    monkeypatch.setattr(ThinkExecutor, "interrupt", interrupt)
    task = asyncio.create_task(conversation.arun())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        conversation.interrupt()
        await asyncio.wait_for(task, timeout=5)
        assert stopped.is_set()
        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
        await conversation.arun()
        history = requests[1]
        assert len([m for m in history if m.tool_calls]) == 1
        assert Counter(m.tool_call_id for m in history if m.role == "tool") == Counter(
            ["call_0", "call_1", "call_2"]
        )
        errors = [
            e for e in conversation.state.events if isinstance(e, AgentErrorEvent)
        ]
        assert Counter(e.tool_call_id for e in errors) == Counter(
            ["call_0", "call_1", "call_2"]
        )
    finally:
        stopped.set()
        if not task.done():
            conversation.interrupt()
        await asyncio.wait_for(task, timeout=5)
