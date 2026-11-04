"""ACP turns keep the agent's text where it was said, between its tool calls."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from openhands.sdk.agent.acp_agent import ACPAgent, _OpenHandsACPBridge
from openhands.sdk.agent.stream_context import StreamStarted
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import (
    ACPToolCallEvent,
    ActionEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.workspace.local import LocalWorkspace


def _text(text: str) -> AgentMessageChunk:
    return AgentMessageChunk(
        session_update="agent_message_chunk",
        content=TextContentBlock(type="text", text=text),
    )


def _thought(text: str) -> AgentThoughtChunk:
    return AgentThoughtChunk(
        session_update="agent_thought_chunk",
        content=TextContentBlock(type="text", text=text),
    )


def _start(tool_call_id: str) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=tool_call_id,
        title=f"run {tool_call_id}",
        kind="execute",
        status="in_progress",
    )


def _done(tool_call_id: str) -> ToolCallProgress:
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=tool_call_id,
        status="completed",
        raw_output="ok",
    )


def _run_turn(tmp_path, updates: list[Any]) -> tuple[list, list]:
    agent = ACPAgent(acp_command=["echo", "test"])
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    state.events.append(
        SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="ACP"), tools=[]
        )
    )
    state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="go")]),
        )
    )
    conversation = MagicMock()
    conversation.state = state
    frames: list = []
    conversation.on_stream = frames.append

    bridge = _OpenHandsACPBridge()
    bridge.get_turn_usage_update = MagicMock(return_value=object())
    agent._client = bridge
    agent._conn = MagicMock()
    agent._session_id = "sess"

    async def _prompt(session_id, prompt):  # noqa: ARG001
        for update in updates:
            await bridge.session_update("sess", update)

    agent._conn.prompt = _prompt
    executor = MagicMock()
    executor.run_async = lambda factory, **_: asyncio.run(factory())
    agent._executor = executor

    events: list = []

    def _on_event(event) -> None:
        events.append(event)
        state.events.append(event)

    agent.step(conversation, on_event=_on_event, on_token=lambda _chunk: None)
    return events, frames


def _summary(events: list) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for event in events:
        if isinstance(event, MessageEvent):
            content = event.llm_message.content[0]
            assert isinstance(content, TextContent)
            rows.append(("message", content.text))
        elif isinstance(event, ACPToolCallEvent):
            rows.append(("tool", f"{event.tool_call_id}:{event.status}"))
        elif isinstance(event, ActionEvent):
            assert isinstance(event.action, FinishAction)
            rows.append(("finish", event.action.message))
        elif isinstance(event, ObservationEvent):
            rows.append(("observation", event.tool_name))
    return rows


def test_text_before_each_tool_call_is_emitted_before_that_call(tmp_path):
    events, _ = _run_turn(
        tmp_path,
        [
            _text("Listing files."),
            _start("ls"),
            _done("ls"),
            _text("Reading the README."),
            _start("cat"),
            _done("cat"),
            _text("Both steps worked."),
        ],
    )

    assert _summary(events) == [
        ("message", "Listing files."),
        ("tool", "ls:in_progress"),
        ("tool", "ls:completed"),
        ("message", "Reading the README."),
        ("tool", "cat:in_progress"),
        ("tool", "cat:completed"),
        ("finish", "Both steps worked."),
        ("observation", "finish"),
    ]


def test_turn_without_tool_calls_still_finishes_with_all_its_text(tmp_path):
    events, _ = _run_turn(tmp_path, [_text("The answer "), _text("is 4.")])

    assert _summary(events) == [
        ("finish", "The answer is 4."),
        ("observation", "finish"),
    ]


def test_consecutive_tool_calls_without_text_between_add_no_empty_message(
    tmp_path,
):
    events, _ = _run_turn(
        tmp_path,
        [_text("Checking."), _start("a"), _start("b"), _done("a"), _done("b")],
    )

    assert _summary(events) == [
        ("message", "Checking."),
        ("tool", "a:in_progress"),
        ("tool", "b:in_progress"),
        ("tool", "a:completed"),
        ("tool", "b:completed"),
        ("finish", ""),
        ("observation", "finish"),
    ]


def test_thoughts_stay_with_the_text_they_preceded(tmp_path):
    events, _ = _run_turn(
        tmp_path,
        [
            _thought("Need the file list."),
            _text("Listing."),
            _start("ls"),
            _done("ls"),
            _thought("Done now."),
            _text("Finished."),
        ],
    )

    message = next(e for e in events if isinstance(e, MessageEvent))
    finish = next(e for e in events if isinstance(e, ActionEvent))
    assert message.llm_message.reasoning_content == "Need the file list."
    assert finish.reasoning_content == "Done now."


def test_each_text_segment_streams_into_its_own_slot_below_earlier_events(
    tmp_path,
):
    events, frames = _run_turn(
        tmp_path,
        [_text("First."), _start("ls"), _done("ls"), _text("Second.")],
    )

    started = [f for f in frames if isinstance(f, StreamStarted)]
    message = next(e for e in events if isinstance(e, MessageEvent))
    finish = next(e for e in events if isinstance(e, ActionEvent))
    assert [s.item_id for s in started] == [message.id, finish.id]
    # The second slot opens after the message and the tool call it follows.
    first_anchor, second_anchor = (s.anchor_seq for s in started)
    assert first_anchor is not None and second_anchor is not None
    assert second_anchor >= first_anchor + 3
