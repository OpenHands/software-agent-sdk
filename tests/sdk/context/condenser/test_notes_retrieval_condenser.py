from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openhands.sdk.context.condenser import NotesRetrievalCondenser
from openhands.sdk.context.condenser.base import (
    CondensationRequirement,
    NoCondensationAvailableException,
)
from openhands.sdk.context.condenser.pipeline_condenser import PipelineCondenser
from openhands.sdk.context.view import View
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import ActionEvent, Event, MessageEvent, ObservationEvent
from openhands.sdk.event.condenser import (
    Condensation,
    CondensationRequest,
    ContextWindowReminderEvent,
    HistoryIndexEvent,
)
from openhands.sdk.event.types import SourceType
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.think import ThinkAction, ThinkObservation


def message_event(text: str, source: SourceType) -> MessageEvent:
    return MessageEvent(
        source=source,
        llm_message=Message(
            role="user" if source == "user" else "assistant",
            content=[TextContent(text=text)],
        ),
    )


@pytest.fixture
def history() -> list[Event]:
    return [
        message_event("original task", source="user"),
        *[message_event(f"progress {i}", source="agent") for i in range(16)],
        message_event("latest instruction", source="user"),
        *[message_event(f"recent {i}", source="agent") for i in range(3)],
    ]


def test_index_preserves_pinned_and_latest_user_without_mutating_view(history):
    condenser = NotesRetrievalCondenser(max_size=20, keep_first=2, keep_recent=2)
    view = View.from_events(history)
    index = condenser.condense(view)
    assert isinstance(index, HistoryIndexEvent)
    assert view.events == history
    reduced = View.from_events([*history, index])
    retained = {event.id for event in reduced.events}
    assert {history[i].id for i in (0, 1, 17, 19, 20)} <= retained
    assert len(reduced) < len(view)
    assert (
        len([event for event in reduced.events if isinstance(event, HistoryIndexEvent)])
        == 1
    )
    assert not any(isinstance(event, Condensation) for event in reduced.events)


def test_reminder_once_per_window_survives_reopen(tmp_path: Path, history):
    condenser = NotesRetrievalCondenser(max_size=20, keep_first=2, keep_recent=2)
    log = EventLog(LocalFileStore(str(tmp_path)))
    for event in history[:16]:
        log.append(event)
    view = View.from_events(log)
    reminder = condenser.get_reminder(view)
    assert isinstance(reminder, ContextWindowReminderEvent)
    log.append(reminder)
    reopened = EventLog(LocalFileStore(str(tmp_path)))
    assert condenser.get_reminder(View.from_events(reopened)) is None

    for event in history[16:]:
        reopened.append(event)
    index = condenser.condense(View.from_events(reopened))
    assert isinstance(index, HistoryIndexEvent)
    reopened.append(index)
    new_view = View.from_events(reopened)
    while len(new_view) < 16:
        event = message_event("more work", source="agent")
        reopened.append(event)
        new_view.append_event(event)
    next_reminder = condenser.get_reminder(new_view)
    assert isinstance(next_reminder, ContextWindowReminderEvent)
    assert next_reminder.window_id == index.id
    assert reopened[reopened.get_index(history[5].id)] == history[5]


@pytest.mark.asyncio
async def test_explicit_request_and_async_pipeline(history):
    condenser = NotesRetrievalCondenser(max_size=100, keep_first=2, keep_recent=2)
    view = View.from_events([*history, CondensationRequest()])
    assert condenser.get_reminder(view) is None
    result = await PipelineCondenser(condensers=[condenser]).acondense(view)
    assert isinstance(result, HistoryIndexEvent)
    view.append_event(result)
    assert not view.unhandled_condensation_request


def test_hard_reset_fails_when_pins_prevent_progress(history):
    condenser = NotesRetrievalCondenser(max_size=20, keep_first=20, keep_recent=2)
    with pytest.raises(NoCondensationAvailableException, match="Cannot reduce"):
        condenser.condense(View.from_events(history))


def test_repeated_windows_replace_old_index(history):
    condenser = NotesRetrievalCondenser(max_size=20, keep_first=2, keep_recent=2)
    view = View.from_events(history)
    for _ in range(3):
        while len(view) < 20:
            view.append_event(message_event("more work", source="agent"))
        index = condenser.condense(view)
        assert isinstance(index, HistoryIndexEvent)
        view.append_event(index)
        assert sum(isinstance(event, HistoryIndexEvent) for event in view.events) == 1


def test_old_condensation_still_replays(history):
    event = Condensation(
        forgotten_event_ids={history[2].id},
        summary="existing summary",
        summary_offset=2,
        llm_response_id="existing-completion",
    )
    view = View.from_events([*history, event])
    assert history[2] not in view.events
    assert view.events[2].to_llm_message().content == [
        TextContent(text="existing summary")
    ]


def test_pinned_action_preserves_entire_parallel_tool_exchange(history):
    actions = [
        ActionEvent(
            thought=[],
            action=ThinkAction(thought=f"plan {i}"),
            tool_name="think",
            tool_call_id=f"call-{i}",
            tool_call=MessageToolCall(
                id=f"call-{i}", name="think", arguments="{}", origin="completion"
            ),
            llm_response_id="parallel-completion",
        )
        for i in range(2)
    ]
    observations = [
        ObservationEvent(
            action_id=action.id,
            tool_name="think",
            tool_call_id=action.tool_call_id,
            observation=ThinkObservation.from_text("recorded"),
        )
        for action in actions
    ]
    events = [history[0], *actions, *observations, *history[1:]]
    view = View.from_events(events)
    index = NotesRetrievalCondenser(max_size=20, keep_first=2, keep_recent=2).condense(
        view
    )
    assert isinstance(index, HistoryIndexEvent)
    reduced = View.from_events([*events, index])
    assert {event.id for event in [*actions, *observations]} <= {
        event.id for event in reduced.events
    }


def test_hard_token_overflow_fails_when_retained_context_does_not_fit(history):
    llm = MagicMock(spec=LLM)
    llm.effective_max_input_tokens = None
    llm.get_token_count.return_value = 500
    condenser = NotesRetrievalCondenser(
        max_size=20, max_tokens=400, keep_first=2, keep_recent=2
    )
    with pytest.raises(NoCondensationAvailableException, match="context budget"):
        condenser.condense(View.from_events(history), agent_llm=llm)


@pytest.mark.parametrize("configured_limit,model_limit", [(1000, 400), (400, 1000)])
def test_token_reminder_and_reset_use_stricter_capacity(
    history, configured_limit, model_limit
):
    llm = MagicMock(spec=LLM)
    llm.effective_max_input_tokens = model_limit
    condenser = NotesRetrievalCondenser(max_tokens=configured_limit)
    view = View.from_events(history)
    llm.get_token_count.return_value = 320
    assert isinstance(condenser.get_reminder(view, llm), ContextWindowReminderEvent)
    assert condenser.condensation_requirement(view, llm) is None
    llm.get_token_count.return_value = 400
    assert condenser.get_reminder(view, llm) is None
    assert condenser.condensation_requirement(view, llm) == CondensationRequirement.HARD
