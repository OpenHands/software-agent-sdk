import json
from pathlib import Path

import pytest

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.context.condenser import NotesRetrievalCondenser
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.event.condenser import (
    CondensationRequest,
    ContextWindowReminderEvent,
    HistoryIndexEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.context_notes import ContextNotesObservation


def tool_message(*calls: tuple[str, dict]) -> Message:
    return Message(
        role="assistant",
        content=[],
        tool_calls=[
            MessageToolCall(
                id=f"call-{i}-{name}",
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
            for i, (name, arguments) in enumerate(calls)
        ],
    )


def notes_agent(llm: TestLLM, *, max_size: int = 30) -> Agent:
    return Agent(
        llm=llm,
        tools=[
            Tool(name="ConversationHistoryTool"),
            Tool(name="ContextNotesTool"),
            Tool(name="NewContextTool"),
        ],
        condenser=NotesRetrievalCondenser(
            max_size=max_size, keep_first=1, keep_recent=2
        ),
    )


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_manual_reset_waits_for_batch_and_notes_survive(
    tmp_path: Path, asynchronous: bool
):
    llm = TestLLM.from_messages(
        [
            tool_message(
                (
                    "context_notes",
                    {"command": "write", "content": "keep this decision"},
                ),
                ("new_context", {}),
            ),
            tool_message(("context_notes", {"command": "read"})),
            tool_message(("finish", {"message": "done"})),
        ]
    )
    conversation = Conversation(
        agent=notes_agent(llm),
        workspace=tmp_path,
        persistence_dir=tmp_path / "history",
        stuck_detection=False,
        visualizer=None,
    )
    conversation.send_message("Continue the task")
    with conversation.state:
        for i in range(8):
            conversation.state.append_event(
                MessageEvent(
                    source="agent",
                    llm_message=Message(
                        role="assistant", content=[TextContent(text=f"progress {i}")]
                    ),
                )
            )
    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()
    events = list(conversation.state.events)
    request_position = next(
        i for i, event in enumerate(events) if isinstance(event, CondensationRequest)
    )
    reset_observation = events[request_position - 1]
    assert isinstance(reset_observation, ObservationEvent)
    assert reset_observation.tool_name == "new_context"
    indexes = [event for event in events if isinstance(event, HistoryIndexEvent)]
    assert len(indexes) == 1
    reads = [
        event.observation
        for event in events
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, ContextNotesObservation)
        and event.observation.command == "read"
    ]
    assert len(reads) == 1
    assert json.loads(reads[0].text)["text"] == "keep this decision"
    assert indexes[0].notes_event_id == reads[0].version_id
    assert not reads[0].is_error
    assert llm.call_count == 3
    conversation.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_reminder_step_does_not_call_llm(tmp_path: Path, asynchronous: bool):
    llm = TestLLM.from_messages([tool_message(("finish", {"message": "done"}))])
    agent = notes_agent(llm, max_size=20)
    conversation = Conversation(agent=agent, workspace=tmp_path, visualizer=None)
    conversation.send_message("Continue")
    with conversation.state:
        while len(conversation.state.view) < 16:
            conversation.state.append_event(
                MessageEvent(
                    source="agent",
                    llm_message=Message(
                        role="assistant", content=[TextContent(text="work")]
                    ),
                )
            )
        collected = []
        if asynchronous:
            await agent.astep(conversation, collected.append)
        else:
            agent.step(conversation, collected.append)
    assert len(collected) == 1
    assert isinstance(collected[0], ContextWindowReminderEvent)
    assert llm.call_count == 0
    conversation.close()


def test_missing_retrieval_tools_fail_during_initialization(tmp_path: Path):
    agent = Agent(
        llm=TestLLM.from_messages([]), condenser=NotesRetrievalCondenser(), tools=[]
    )
    with pytest.raises(ValueError, match="conversation_history"):
        Conversation(agent=agent, workspace=tmp_path).send_message("hello")
