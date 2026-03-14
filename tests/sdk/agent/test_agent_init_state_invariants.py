from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import (
    ConversationStateUpdateEvent,
    MessageEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import LLM, TextContent


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
    from openhands.sdk.llm import Message

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
