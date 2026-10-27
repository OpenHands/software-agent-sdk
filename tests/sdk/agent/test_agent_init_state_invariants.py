from __future__ import annotations

import uuid
from typing import Literal

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.agent.utils import aprepare_llm_messages, prepare_llm_messages
from openhands.sdk.context.view import View
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import (
    ConversationStateUpdateEvent,
    Event,
    HookExecutionEvent,
    MessageEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import LLM, Message, TextContent


def _make_agent() -> Agent:
    llm = LLM(model="test-model", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm)


def _make_state(agent: Agent, tmp_path) -> ConversationState:
    from openhands.sdk.workspace.local import LocalWorkspace

    return ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )


def test_agent_init_state_adds_system_prompt_via_callback(tmp_path) -> None:
    agent = _make_agent()
    state = _make_state(agent, tmp_path)

    emitted: list[SystemPromptEvent] = []

    def on_event(e):
        if isinstance(e, SystemPromptEvent):
            emitted.append(e)

    agent.init_state(state, on_event=on_event)

    assert len(emitted) == 1
    assert isinstance(emitted[0], SystemPromptEvent)


def test_agent_init_state_skips_when_system_prompt_already_present(tmp_path) -> None:
    agent = _make_agent()
    state = _make_state(agent, tmp_path)
    state.events.append(
        SystemPromptEvent(
            source="agent",
            system_prompt=TextContent(text="x"),
            tools=[],
        )
    )

    called = False

    def on_event(_e):
        nonlocal called
        called = True

    agent.init_state(state, on_event=on_event)

    assert called is False


def test_agent_init_state_skips_when_system_prompt_is_second_event_remote_prefix(
    tmp_path,
) -> None:
    agent = _make_agent()
    state = _make_state(agent, tmp_path)
    state.events.append(ConversationStateUpdateEvent(key="stats", value={}))
    state.events.append(
        SystemPromptEvent(
            source="agent",
            system_prompt=TextContent(text="x"),
            tools=[],
        )
    )

    called = False

    def on_event(_e):
        nonlocal called
        called = True

    agent.init_state(state, on_event=on_event)

    assert called is False


def test_agent_init_state_raises_if_user_message_before_system_prompt_in_prefix(
    tmp_path,
) -> None:
    agent = _make_agent()
    state = _make_state(agent, tmp_path)
    state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="hi")]),
        )
    )

    with pytest.raises(
        AssertionError, match=r"user message exists before SystemPromptEvent"
    ):
        agent.init_state(state, on_event=lambda _e: None)


def _non_llm_preamble() -> list[Event]:
    """Bootstrap prefix from Agent Canvas: two state syncs, then SessionStart."""
    return [
        ConversationStateUpdateEvent(key="stats", value={}),
        ConversationStateUpdateEvent(key="execution_status", value="idle"),
        HookExecutionEvent(
            hook_event_type="SessionStart",
            hook_command="true",
            success=True,
            exit_code=0,
        ),
    ]


def _message(
    source: Literal["agent", "user"],
    role: Literal["user", "assistant"],
    text: str,
) -> MessageEvent:
    return MessageEvent(
        source=source,
        llm_message=Message(role=role, content=[TextContent(text=text)]),
    )


async def test_agent_init_state_skips_system_prompt_after_non_llm_preamble(
    tmp_path,
) -> None:
    agent = _make_agent()
    state = _make_state(agent, tmp_path)
    for event in [
        *_non_llm_preamble(),
        ConversationStateUpdateEvent(key="stats", value={}),
        SystemPromptEvent(system_prompt=TextContent(text="original"), tools=[]),
        _message("user", "user", "task"),
        _message("agent", "assistant", "done"),
    ]:
        state.events.append(event)

    emitted: list[Event] = []
    agent.init_state(state, on_event=emitted.append)
    agent.init_state(state, on_event=emitted.append)

    assert emitted == []
    system_events = [
        event for event in state.events if isinstance(event, SystemPromptEvent)
    ]
    assert len(system_events) == 1

    view = View.from_events(state.events)
    messages = prepare_llm_messages(view)
    async_messages = await aprepare_llm_messages(view)
    assert isinstance(messages, list)
    assert isinstance(async_messages, list)
    assert [message.role for message in messages] == ["system", "user", "assistant"]
    assert [message.role for message in async_messages] == [
        "system",
        "user",
        "assistant",
    ]


def test_agent_init_state_rejects_user_message_after_non_llm_preamble(
    tmp_path,
) -> None:
    agent = _make_agent()
    state = _make_state(agent, tmp_path)
    for event in _non_llm_preamble():
        state.events.append(event)
    state.events.append(_message("user", "user", "task"))

    emitted: list[Event] = []
    with pytest.raises(
        AssertionError, match="user message exists before SystemPromptEvent"
    ):
        agent.init_state(state, on_event=emitted.append)

    assert emitted == []


def test_agent_init_state_rejects_user_message_before_later_system_prompt(
    tmp_path,
) -> None:
    agent = _make_agent()
    state = _make_state(agent, tmp_path)
    for event in [
        *_non_llm_preamble(),
        _message("user", "user", "task"),
        SystemPromptEvent(system_prompt=TextContent(text="too late"), tools=[]),
    ]:
        state.events.append(event)

    emitted: list[Event] = []
    with pytest.raises(
        AssertionError, match="user message exists before SystemPromptEvent"
    ):
        agent.init_state(state, on_event=emitted.append)

    assert emitted == []
    assert sum(isinstance(event, SystemPromptEvent) for event in state.events) == 1
