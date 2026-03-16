from __future__ import annotations

from collections.abc import Iterator

import pytest

from openhands.sdk.agent.agent import Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.workspace.local import LocalWorkspace


class _LimitedIterEvents(EventLog):
    def __init__(self, events, max_iter: int):
        self._events = list(events)
        self._max_iter = max_iter
        self._iter_count = 0

    def __len__(self) -> int:  # type: ignore[override]
        return len(self._events)

    def __getitem__(self, idx):  # type: ignore[override]
        return self._events[idx]

    def __iter__(self) -> Iterator:  # type: ignore[override]
        self._iter_count += 1
        if self._iter_count > self._max_iter:
            raise AssertionError("events iterated too many times")
        return iter(self._events)

    def append(self, event) -> None:  # type: ignore[override]
        self._events.append(event)


class _FailingIterEvents(EventLog):
    def __init__(self, events):
        self._events = list(events)

    def __len__(self) -> int:  # type: ignore[override]
        return len(self._events)

    def __getitem__(self, idx):  # type: ignore[override]
        return self._events[idx]

    def __iter__(self) -> Iterator:  # type: ignore[override]
        raise AssertionError("events iterated unexpectedly")

    def append(self, event) -> None:  # type: ignore[override]
        self._events.append(event)


def test_agent_step_latest_user_message_scan_is_bounded(tmp_path):
    agent = Agent(llm=LLM(model="gpt-4o-mini", api_key="x"), tools=[])
    workspace = LocalWorkspace(working_dir=tmp_path)
    conv = LocalConversation(agent=agent, workspace=workspace)

    # Create a long-ish history with the user message at the end.
    for i in range(1000):
        conv._on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text=str(i))]
                ),
            )
        )

    conv.send_message("hi")
    blocked_user_msg = conv.state.events[-1]

    conv.state.block_message(blocked_user_msg.id, "blocked")

    # Replace the events list with a wrapper that would blow up if code iterates
    # over the full history via list(state.events).
    conv.state._events = _LimitedIterEvents(conv.state.events, max_iter=0)

    agent.step(conv, on_event=conv._on_event)

    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED


def test_agent_step_uses_last_user_message_id(tmp_path):
    agent = Agent(llm=LLM(model="gpt-4o-mini", api_key="x"), tools=[])
    workspace = LocalWorkspace(working_dir=tmp_path)
    conv = LocalConversation(agent=agent, workspace=workspace)

    conv.send_message("hi")
    message = conv.state.events[-1]

    conv.state.block_message(message.id, "blocked")

    conv.state._events = _FailingIterEvents(conv.state.events)

    agent.step(conv, on_event=conv._on_event)

    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED


def test_agent_step_legacy_state_no_last_user_id(tmp_path, caplog):
    """Verify graceful handling of old state without last_user_message_id.

    When last_user_message_id is None but blocked_messages exist (legacy state),
    the code should log a debug message and continue processing rather than
    checking for blocked messages.
    """
    import logging

    agent = Agent(llm=LLM(model="gpt-4o-mini", api_key="x"), tools=[])
    workspace = LocalWorkspace(working_dir=tmp_path)
    conv = LocalConversation(agent=agent, workspace=workspace)

    conv.send_message("hi")
    message = conv.state.events[-1]

    # Simulate legacy state: blocked_messages exist but last_user_message_id is None
    conv.state.block_message(message.id, "blocked by hook")
    conv.state.last_user_message_id = None

    # Capture debug logs
    with caplog.at_level(logging.DEBUG, logger="openhands.sdk.agent.agent"):
        # Step should NOT finish early since we can't check blocked messages
        # without last_user_message_id. It will proceed to LLM call which will
        # fail due to invalid API key, but that's expected.
        try:
            agent.step(conv, on_event=conv._on_event)
        except Exception:
            # Expected: LLM call fails with invalid API key
            pass

    # Verify the legacy fallback debug message was logged
    assert any(
        "Blocked messages exist but last_user_message_id is None" in record.message
        for record in caplog.records
    )

    # Verify blocked_messages was NOT consumed (since we skipped the check)
    assert message.id in conv.state.blocked_messages


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
