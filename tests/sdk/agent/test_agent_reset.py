import json

import pytest

from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import (
    Condensation,
    CondensationRequest,
    Event,
    InterruptEvent,
    MessageEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.llm.exceptions import LLMContextWindowExceedError
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import Tool
from openhands.sdk.tool.builtins.new_context import (
    NewContextObservation,
)

from .conftest import response


async def run(conversation, asynchronous):
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_reset_preserves_handoff_and_recovers_omitted_history(
    scripted_conversation, asynchronous
):
    conversation, requests = scripted_conversation(
        [
            response(
                calls=[("new_context", {"handoff": "Continue testing the retry fix."})],
                response_id="reset-response",
            ),
            response(
                calls=[
                    (
                        "conversation_history",
                        {"command": "search", "query": "Retry-After"},
                    )
                ],
                response_id="search-response",
            ),
            response(
                text="The original Retry-After value was 27 seconds.",
                response_id="final-response",
            ),
        ]
    )
    conversation.send_message("Fix retry handling. Evidence: Retry-After: 27.")
    await run(conversation, asynchronous)

    assert len(requests) == 3
    after_reset = str(requests[1])
    assert "Continue testing the retry fix." in after_reset
    assert "Retry-After: 27" not in after_reset
    assert "Retry-After: 27" in str(requests[2])
    events = list(conversation.state.events)
    condensations = [e for e in events if isinstance(e, Condensation)]
    assert len(condensations) == 1
    assert condensations[0].summary is None
    assert condensations[0].llm_response_id == "reset-response"
    assert any("Retry-After: 27" in str(e) for e in events)
    result_index = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, ObservationEvent) and e.tool_name == "new_context"
    )
    request_index = next(
        i for i, e in enumerate(events) if isinstance(e, CondensationRequest)
    )
    assert result_index < request_index < events.index(condensations[0])


async def test_reset_keeps_user_message_arriving_during_model_request(
    scripted_conversation,
):
    def inject(index):
        if index == 1:
            conversation.send_message("Do not change the public interface.")

    conversation, requests = scripted_conversation(
        [
            response(calls=[("new_context", {"handoff": "Continue with tests."})]),
            response(text="Done."),
        ],
        before=inject,
    )
    conversation.send_message("Old task description.")
    await conversation.arun()
    assert "Old task description." not in str(requests[1])
    assert "Do not change the public interface." in str(requests[1])


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("retry_fails", [False, True])
async def test_provider_overflow_gets_only_one_summary_rescue(
    scripted_conversation, asynchronous, retry_fails
):
    overflow = LLMContextWindowExceedError("provider capacity exceeded")
    script = [
        response(
            calls=[("think", {"thought": "Old implementation details. " * 200})],
            response_id="work-response",
        ),
        overflow,
        response(
            text="Implementation inspected; continue with tests.",
            response_id="summary-response",
        ),
        overflow if retry_fails else response(text="Done.", response_id="done"),
    ]
    conversation, requests = scripted_conversation(script)
    conversation.send_message("Keep the public interface unchanged.")
    if retry_fails:
        with pytest.raises(ConversationRunError):
            await run(conversation, asynchronous)
    else:
        await run(conversation, asynchronous)
    assert len(requests) == 4
    assert "Keep the public interface unchanged." in str(requests[-1])
    condensations = [
        e for e in conversation.state.events if isinstance(e, Condensation)
    ]
    assert len(condensations) == 1
    assert condensations[0].llm_response_id == "summary-response"
    assert condensations[0].summary == "Implementation inspected; continue with tests."


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_multiple_reset_calls_coalesce_after_the_whole_batch(
    scripted_conversation, asynchronous
):
    conversation, requests = scripted_conversation(
        [
            response(
                calls=[
                    ("new_context", {"handoff": "First handoff."}),
                    ("think", {"thought": "Finish checking the change."}),
                    ("new_context", {"handoff": "Second handoff."}),
                ]
            ),
            response(text="Done."),
        ]
    )
    conversation.send_message("Original request.")
    await run(conversation, asynchronous)
    assert "First handoff." in str(requests[1])
    assert "Second handoff." in str(requests[1])
    events = list(conversation.state.events)
    assert sum(isinstance(e, Condensation) for e in events) == 1
    request_index = next(
        i for i, e in enumerate(events) if isinstance(e, CondensationRequest)
    )
    assert sum(isinstance(e, ObservationEvent) for e in events[:request_index]) == 3


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_finish_takes_precedence_over_reset(scripted_conversation, asynchronous):
    conversation, requests = scripted_conversation(
        [
            response(
                calls=[
                    ("new_context", {"handoff": "A handoff."}),
                    ("finish", {"message": "All done."}),
                ]
            ),
        ]
    )
    conversation.send_message("Original request.")
    await run(conversation, asynchronous)
    assert len(requests) == 1
    assert not any(
        isinstance(e, (Condensation, CondensationRequest))
        for e in conversation.state.events
    )


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("gap", ["before_request", "before_reset", "after_reset"])
async def test_reset_recovers_persisted_commit_gaps(
    scripted_conversation, reopen_conversation, monkeypatch, asynchronous, gap
):
    conversation, requests = scripted_conversation(
        [
            response(
                calls=[("new_context", {"handoff": "Continue the saved plan."})],
                response_id="reset-response",
            ),
            response(text="Resumed work complete.", response_id="done"),
        ]
    )
    conversation.send_message("Original task details.")
    append_event = ConversationState.append_event
    failed = False

    def fail_commit(state: ConversationState, event: Event) -> int:
        nonlocal failed
        target = CondensationRequest if gap == "before_request" else Condensation
        if state is conversation.state and isinstance(event, target) and not failed:
            failed = True
            if gap == "after_reset":
                append_event(state, event)
            raise RuntimeError("Simulated process failure at a reset commit boundary")
        return append_event(state, event)

    with monkeypatch.context() as failure:
        failure.setattr(ConversationState, "append_event", fail_commit)
        with pytest.raises(ConversationRunError):
            await run(conversation, asynchronous)

    assert failed
    restored = reopen_conversation(conversation)
    await run(restored, asynchronous)

    assert len(requests) == 2
    assert "Continue the saved plan." in str(requests[-1])
    assert "Original task details." not in str(requests[-1])
    events = list(restored.state.events)
    assert sum(isinstance(event, CondensationRequest) for event in events) == 1
    assert sum(isinstance(event, Condensation) for event in events) == 1
    results = [
        event
        for event in events
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, NewContextObservation)
    ]
    assert len(results) == 1
    observation = results[0].observation
    assert isinstance(observation, NewContextObservation)
    assert observation.input_event_id is not None
    boundary = next(event for event in events if event.id == observation.input_event_id)
    assert isinstance(boundary, MessageEvent)
    assert "Original task details." in str(boundary)
    assert restored.state.view.pending_condensation_request is None


async def test_fork_retains_reset_boundary_and_isolates_future_history(
    scripted_conversation,
):
    conversation, requests = scripted_conversation(
        [
            response(
                calls=[("new_context", {"handoff": "Continue on this branch."})],
                response_id="reset",
            ),
            response(text="Source complete.", response_id="source-done"),
            response(
                calls=[
                    (
                        "conversation_history",
                        {"command": "search", "query": "SOURCE-ONLY"},
                    )
                ],
                response_id="search-private",
            ),
            response(
                calls=[
                    (
                        "conversation_history",
                        {"command": "search", "query": "ORIGINAL-EVIDENCE"},
                    )
                ],
                response_id="search-original",
            ),
            response(text="Fork complete.", response_id="fork-done"),
        ]
    )
    conversation.send_message("ORIGINAL-EVIDENCE: preserve the retry contract.")
    await conversation.arun()
    request = next(
        event
        for event in conversation.state.events
        if isinstance(event, CondensationRequest)
    )
    conversation.send_message("SOURCE-ONLY: a later private branch instruction.")
    source_ids = {event.id for event in conversation.state.events}
    fork = conversation.fork(from_event_id=request.id)
    try:
        fork.send_message("FORK-ONLY: check the new tests.")
        await fork.arun()
        assert "FORK-ONLY" in str(requests[2])
        assert "ORIGINAL-EVIDENCE" not in str(requests[2])
        assert not any(
            "SOURCE-ONLY" in str(event)
            for event in fork.state.active_branch()
            if isinstance(event, MessageEvent)
        )
        observations = [
            event.observation
            for event in fork.state.events
            if isinstance(event, ObservationEvent)
            and event.tool_name == "conversation_history"
        ]
        assert json.loads(observations[0].text)["matches"] == []
        matches = json.loads(observations[1].text)["matches"]
        assert any("ORIGINAL-EVIDENCE" in match["snippet"] for match in matches)
        assert {event.id for event in conversation.state.events} == source_ids
        assert sum(isinstance(event, Condensation) for event in fork.state.events) == 1
    finally:
        fork.close()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_rejected_reset_does_not_change_context(
    scripted_conversation, asynchronous
):
    conversation, requests = scripted_conversation(
        [
            response(calls=[("new_context", {"handoff": "A rejected handoff."})]),
            response(text="Continuing without reset.", response_id="done"),
        ]
    )
    conversation.set_confirmation_policy(AlwaysConfirm())
    conversation.send_message("Keep this original context.")
    await run(conversation, asynchronous)
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    conversation.reject_pending_actions("Keep working in the current context.")
    await run(conversation, asynchronous)

    assert "Keep this original context." in str(requests[-1])
    assert any(
        isinstance(event, UserRejectObservation) for event in conversation.state.events
    )
    assert not any(
        isinstance(event, (CondensationRequest, Condensation))
        for event in conversation.state.events
    )


async def test_interrupted_reset_does_not_reappear_on_resume(
    scripted_conversation, reopen_conversation
):
    def interrupt_after_result(event):
        if isinstance(event, ObservationEvent) and isinstance(
            event.observation, NewContextObservation
        ):
            conversation.interrupt()

    conversation, requests = scripted_conversation(
        [
            response(calls=[("new_context", {"handoff": "Interrupted handoff."})]),
            response(
                text="Continue without the interrupted reset.", response_id="done"
            ),
        ],
        callbacks=[interrupt_after_result],
    )
    conversation.send_message("Retain the original request after interruption.")
    await conversation.arun()

    assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
    assert any(
        isinstance(event, ObservationEvent)
        and isinstance(event.observation, NewContextObservation)
        and not event.observation.is_error
        for event in conversation.state.events
    )
    assert any(isinstance(event, InterruptEvent) for event in conversation.state.events)
    assert not any(
        isinstance(event, (CondensationRequest, Condensation))
        for event in conversation.state.events
    )
    restored = reopen_conversation(conversation)
    await restored.arun()
    assert "Retain the original request after interruption." in str(requests[-1])
    assert not any(
        isinstance(event, (CondensationRequest, Condensation))
        for event in restored.state.events
    )


@pytest.mark.parametrize(
    "tool_name, missing",
    [
        ("NewContextTool", "conversation_history"),
        ("ConversationHistoryTool", "new_context"),
    ],
)
def test_reset_requires_both_tools_at_initialization(
    scripted_conversation, tool_name, missing
):
    conversation, requests = scripted_conversation([], tools=[Tool(name=tool_name)])
    with pytest.raises(ValueError, match=f"Condenser requires tools: {missing}"):
        conversation.send_message("Start the task.")
    assert requests == []
