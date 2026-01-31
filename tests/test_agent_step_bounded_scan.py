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

    blocked_user_msg = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hi")]),
    )
    conv._on_event(blocked_user_msg)

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

    message = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hi")]),
    )
    conv._on_event(message)

    conv.state.last_user_message_id = message.id
    conv.state.block_message(message.id, "blocked")

    conv.state._events = _FailingIterEvents(conv.state.events)

    agent.step(conv, on_event=conv._on_event)

    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
