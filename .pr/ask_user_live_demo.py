"""End-to-end live evidence for the ask_user pause/resume flow (PR evidence).

Runs a real LocalConversation with a TestLLM that scripted the agent calling
``ask_user`` and then ``finish``. Prints the observable state at each step so a
reviewer can see: the run pauses at WAITING_FOR_CONFIRMATION with one pending
request, the answer sent via ``send_message()`` is consumed as the tool
observation, and the run resumes to FINISHED.

Run:
    uv run python .pr/ask_user_live_demo.py
"""

import json

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.event.llm_convertible.observation import AgentErrorEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins import AskUserTool
from openhands.sdk.tool.builtins.ask_user import AskUserObservation


ASK_ARGS = json.dumps(
    {
        "questions": [
            {
                "question": "Which auth?",
                "header": "auth",
                "options": [
                    {"id": "jwt", "label": "JWT", "description": "token"},
                    {"id": "session", "label": "Session", "description": "cookie"},
                ],
                "multiSelect": False,
            }
        ]
    }
)


def call(name: str, call_id: str, arguments: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=call_id, name=name, arguments=arguments, origin="completion"
            )
        ],
    )


llm = TestLLM.from_messages(
    [
        call("ask_user", "ask_1", ASK_ARGS),
        call("finish", "finish_1", '{"message": "done"}'),
    ]
)
agent = Agent(llm=llm, tools=[Tool(name=AskUserTool.name)])
conversation: LocalConversation = Conversation(agent=agent)

print("== turn 1: agent calls ask_user ==")
conversation.send_message("add auth to the API")
conversation.run()
status = conversation.state.execution_status
print("status after run:", status)
assert status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION

pending = ConversationState.get_unmatched_actions(conversation.state.events)
print("pending requests:", [(p.tool_name, p.id) for p in pending])
assert len(pending) == 1 and pending[0].tool_name == "ask_user"
request_id = pending[0].id

print("\n== turn 2: user answers via send_message() ==")
conversation.send_message(
    json.dumps(
        {
            "request_id": request_id,
            "action": "accept",
            "answers": {"auth": [{"option_id": "jwt"}]},
        }
    )
)
remaining = ConversationState.get_unmatched_actions(conversation.state.events)
print("pending after answer:", remaining)
assert remaining == []

observations = [
    e.observation
    for e in conversation.state.events
    if isinstance(e, ObservationEvent) and isinstance(e.observation, AskUserObservation)
]
print("ask_user observations:", [o.model_dump() for o in observations])
assert len(observations) == 1
assert observations[0].answers[0].selected[0].option_id == "jwt"

user_texts = [
    c.text
    for e in conversation.state.events
    if isinstance(e, MessageEvent) and e.source == "user"
    for c in e.llm_message.content
    if isinstance(c, TextContent)
]
print("user turns in history:", user_texts)
assert user_texts == ["add auth to the API"], "answer must not become a user turn"

print("\n== turn 3: resume the run ==")
conversation.run()
print("final status:", conversation.state.execution_status)
assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
print("\nLIVE DEMO PASSED")


# ---------------------------------------------------------------------------
# Scenario 2: an invalid call gets a corrective observation, not a crash.
# ---------------------------------------------------------------------------
bad_args = json.dumps(
    {
        "questions": [
            {
                "question": "Which auth?",
                "header": "auth",
                "options": [{"id": "jwt", "label": "JWT", "description": "token"}],
            }
        ]
    }
)
llm2 = TestLLM.from_messages(
    [
        call("ask_user", "ask_bad", bad_args),
        call("finish", "finish_2", '{"message": "fixed"}'),
    ]
)
conversation2: LocalConversation = Conversation(
    agent=Agent(llm=llm2, tools=[Tool(name=AskUserTool.name)])
)
print("\n== scenario 2: invalid ask_user call (1 option) ==")
conversation2.send_message("ask me")
conversation2.run()
errors = [e.error for e in conversation2.state.events if isinstance(e, AgentErrorEvent)]
print("corrective errors:", errors)
assert any("between 2 and 4 options" in e for e in errors)
assert conversation2.state.execution_status == ConversationExecutionStatus.FINISHED
print("\nSCENARIO 2 PASSED")
