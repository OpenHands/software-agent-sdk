import pytest

from openhands.sdk import LLM
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.event import ActionEvent, Condensation, ContextWindowReminderEvent


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_reminder_is_once_per_window_and_reset_allows_another(
    monkeypatch, scripted_conversation, response_factory, asynchronous
):
    counted_tools = []

    def count(llm, messages, tools=None, add_security_risk_prediction=False):
        counted_tools.append({tool.name for tool in tools or []})
        return 17_000

    monkeypatch.setattr(LLM, "get_token_count", count)
    conversation, requests = scripted_conversation(
        [
            response_factory(
                calls=[("new_context", {"handoff": "Continue."})], response_id="reset"
            ),
            response_factory(text="Done.", response_id="done"),
            response_factory(text="Still done.", response_id="follow-up"),
        ],
        max_input_tokens=20_000,
    )
    conversation.send_message("Work on the task.")
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()
    reminders = [
        e
        for e in conversation.state.events
        if isinstance(e, ContextWindowReminderEvent)
    ]
    assert len(reminders) == 2
    assert len(requests) == 2
    assert all(
        {"new_context", "conversation_history"} <= tools for tools in counted_tools
    )
    conversation.send_message("Confirm completion.")
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()
    assert (
        sum(
            isinstance(e, ContextWindowReminderEvent) for e in conversation.state.events
        )
        == 2
    )


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_known_overflow_is_recovered_before_sending_main_request(
    monkeypatch, scripted_conversation, response_factory, asynchronous
):
    def count(llm, messages, **kwargs):
        return 20_000 if "TOO_LARGE" in str(messages) else 1_000

    monkeypatch.setattr(LLM, "get_token_count", count)
    conversation, requests = scripted_conversation(
        [
            response_factory(
                calls=[("think", {"thought": "TOO_LARGE " * 400})], response_id="work"
            ),
            response_factory(
                text="The implementation was inspected.", response_id="summary"
            ),
            response_factory(text="Done.", response_id="done"),
        ],
        max_input_tokens=20_000,
    )
    conversation.send_message("Keep the public API unchanged.")
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()
    assert len(requests) == 3
    assert "Keep the public API unchanged." in str(requests[-1])
    condensations = [
        e for e in conversation.state.events if isinstance(e, Condensation)
    ]
    assert len(condensations) == 1
    assert condensations[0].summary == "The implementation was inspected."
    assert not any(
        isinstance(e, ContextWindowReminderEvent) for e in conversation.state.events
    )


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_oversized_handoff_fails_without_summarizing_or_committing_reset(
    monkeypatch, scripted_conversation, response_factory, asynchronous
):
    def count(llm, messages, **kwargs):
        return 20_000 if "PROTECTED_HANDOFF" in str(messages) else 1_000

    monkeypatch.setattr(LLM, "get_token_count", count)
    conversation, requests = scripted_conversation(
        [
            response_factory(calls=[("new_context", {"handoff": "PROTECTED_HANDOFF"})]),
        ],
        max_input_tokens=20_000,
    )
    conversation.send_message("Original task.")
    with pytest.raises(ConversationRunError, match="(?i)(capacity|protected)"):
        if asynchronous:
            await conversation.arun()
        else:
            conversation.run()
    assert len(requests) == 1
    assert not any(isinstance(e, Condensation) for e in conversation.state.events)
    assert any(
        "PROTECTED_HANDOFF" in event.tool_call.arguments
        for event in conversation.state.events
        if isinstance(event, ActionEvent)
    )


def test_user_input_alone_over_capacity_stops_without_an_llm_call(
    monkeypatch, scripted_conversation
):
    monkeypatch.setattr(LLM, "get_token_count", lambda *args, **kwargs: 20_000)
    conversation, requests = scripted_conversation([], max_input_tokens=20_000)
    conversation.send_message("Too much protected input.")
    with pytest.raises(ConversationRunError, match="(?i)(preserv|history)"):
        conversation.run()
    assert requests == []
