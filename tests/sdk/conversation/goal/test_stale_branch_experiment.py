import json

import pytest

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.goal import GoalAsked, GoalOutcome, run_goal
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.event.ask_user import find_pending_ask
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
from openhands.tools.ask_user import AskUserTool


def text(value):
    return Message(role="assistant", content=[TextContent(text=value)])


def setup_conversation(tmp_path):
    ask = Message(
        role="assistant",
        content=[],
        tool_calls=[
            MessageToolCall(
                id="call_question",
                name="ask_user",
                origin="completion",
                arguments=json.dumps(
                    {
                        "question": "Keep old branch?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }
                ),
            )
        ],
    )
    model = TestLLM.from_messages([ask, text("New objective done")])
    conversation = Conversation(
        agent=Agent(llm=model, tools=[Tool(name=AskUserTool.name)]),
        workspace=str(tmp_path),
        callbacks=[],
        visualizer=None,
        persistence_dir=str(tmp_path / "state"),
    )
    conversation.send_message("Initial task")
    root = next(
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    )
    conversation.run()
    question = find_pending_ask(conversation.state.active_branch())
    assert question is not None
    shown = next(
        e
        for e in conversation.state.events
        if isinstance(e, ObservationEvent) and e.tool_name == "ask_user"
    )
    return conversation, model, root, shown, question


@pytest.mark.parametrize(
    "mode",
    [
        "abandoned-question",
        "current-pending",
        "abandoned-answer",
        "current-answer",
        "empty-head",
    ],
)
def test_goal_branch_selection(tmp_path, mode):
    conversation, model, root, shown, question = setup_conversation(tmp_path)
    try:
        if mode == "abandoned-question":
            conversation.navigate_to(root.id)
        if mode == "empty-head":
            conversation.navigate_to(None)
            assert conversation.state.active_branch() == []
        if mode in ("abandoned-answer", "current-answer"):
            conversation.send_message("Keep it")
        if mode == "abandoned-answer":
            conversation.navigate_to(shown.id)
        count = len(conversation.state.events)
        before_calls = model.call_count
        judge = TestLLM.from_messages(
            [text('{"score":1.0,"complete":true,"missing":""}')]
        )
        result = run_goal(conversation, "NEW OBJECTIVE", judge)
        print(
            json.dumps(
                {
                    "case": mode,
                    "result": type(result).__name__,
                    "agent_calls": model.call_count - before_calls,
                    "judge_calls": judge.call_count,
                    "events_added": len(conversation.state.events) - count,
                    "question": getattr(result, "question", None),
                }
            )
        )
        if mode in ("current-pending", "abandoned-answer"):
            assert isinstance(result, GoalAsked)
            assert result.ask_id == question.ask_id
            assert len(conversation.state.events) == count
            assert model.call_count == before_calls
            assert judge.call_count == 0
        else:
            assert isinstance(result, GoalOutcome)
            assert result.status == "complete"
            assert model.call_count == before_calls + 1
            assert judge.call_count == 1
            assert any(
                isinstance(e, MessageEvent) and e.sender == "goal"
                for e in conversation.state.active_branch()
            )
    finally:
        conversation.close()
