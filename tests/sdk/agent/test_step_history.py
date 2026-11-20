import logging
from collections import Counter
from contextlib import closing

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM


@pytest.mark.asyncio
@pytest.mark.parametrize("run_async", [False, True])
async def test_agent_steps_read_recent_tool_history(run_async, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    call_ids = [f"call_{index}" for index in range(10)]
    replies: list[Message | Exception] = [
        Message(
            role="assistant",
            content=[],
            tool_calls=[
                MessageToolCall(
                    id=call_id,
                    name="think",
                    arguments='{"thought":"Continue"}',
                    origin="completion",
                )
            ],
        )
        for call_id in call_ids
    ]
    llm = TestLLM.from_messages(
        replies + [Message(role="assistant", content=[TextContent(text="Done")])]
    )
    agent = Agent(llm=llm, tools=[], include_default_tools=["ThinkTool", "FinishTool"])
    visited = Counter()
    walk_back = EventLog.walk_back

    def count_actions(self, *args, **kwargs):
        for event in walk_back(self, *args, **kwargs):
            if isinstance(event, ActionEvent):
                visited[event.tool_call_id] += 1
            yield event

    monkeypatch.setattr(EventLog, "walk_back", count_actions)
    conversation = LocalConversation(
        agent=agent, workspace=str(tmp_path), visualizer=None, stuck_detection=False
    )
    with closing(conversation):
        conversation.send_message("Perform ten checks.")
        if run_async:
            await conversation.arun()
        else:
            conversation.run()
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        assert [
            event.tool_call_id
            for event in conversation.state.events
            if isinstance(event, ObservationEvent)
        ] == call_ids
    assert visited[call_ids[0]] <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("run_async", [False, True])
async def test_agent_logs_prompt_size_without_content(
    run_async, caplog, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    prompt = "Unique fixture text for the model"
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Done")])]
    )
    agent = Agent(llm=llm, tools=[])
    conversation = LocalConversation(
        agent=agent, workspace=str(tmp_path), visualizer=None
    )
    with closing(conversation), caplog.at_level(logging.DEBUG, logger=Agent.__module__):
        conversation.send_message(prompt)
        if run_async:
            await conversation.arun()
        else:
            conversation.run()
        assert llm.call_count == 1
    logs = [
        record.getMessage()
        for record in caplog.records
        if record.name == Agent.__module__
        and record.getMessage().startswith("Sending ")
    ]
    assert logs == ["Sending 2 messages to LLM"]
