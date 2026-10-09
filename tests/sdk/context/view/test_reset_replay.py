import pytest

from openhands.sdk.context.view import View
from openhands.sdk.event import (
    Condensation,
    CondensationRequest,
    ContextWindowReminderEvent,
    Event,
    MessageEvent,
)
from openhands.sdk.llm import Message, TextContent


@pytest.mark.parametrize("remove_history", [False, True])
def test_replay_consumes_reset_and_only_rearms_reminder_after_change(remove_history):
    message = MessageEvent(
        llm_message=Message(
            role="user", content=[TextContent(text="Original request")]
        ),
        source="user",
    )
    request = CondensationRequest(trigger_action_id="reset-action")
    reminder = ContextWindowReminderEvent()
    history = [message, reminder, request]
    pending = View.from_events(history)
    assert pending.pending_condensation_request == request
    assert pending.unhandled_condensation_request
    assert pending.context_window_reminded

    result = Condensation(
        forgotten_event_ids={message.id} if remove_history else set(),
        llm_response_id="real-response",
    )
    replayed = View.from_events(
        [
            Event.model_validate_json(event.model_dump_json())
            for event in [*history, result]
        ]
    )

    assert replayed.pending_condensation_request is None
    assert not replayed.unhandled_condensation_request
    assert replayed.context_window_reminded is not remove_history
    assert (message in replayed.events) is not remove_history
