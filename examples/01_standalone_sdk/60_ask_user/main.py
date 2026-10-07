"""Ask the user structured questions mid-task, then resume with the answer.

The ``ask_user`` tool lets the agent pause and ask one or more questions, each
with a short header, 2-4 labelled options, and an always-available free-text
"Other" escape hatch. The call ends the run at ``WAITING_FOR_CONFIRMATION`` and
records a pending request; the next message sent through ``send_message()`` that
parses as an answer is consumed as the tool's observation, and the run resumes
with that observation in history.

This example drives the flow end to end: it sends a task that forces a choice,
detects the pause, answers the request programmatically (picking an option and
demonstrating the free-text escape hatch), and resumes the agent.

Usage:
    LLM_API_KEY=... LLM_BASE_URL=https://llm-proxy.app.all-hands.dev \
        uv run python examples/01_standalone_sdk/60_ask_user/main.py
"""

import json
import os
import tempfile

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.tool.builtins import AskUserTool
from openhands.sdk.tool.builtins.ask_user import AskUserAction


model = os.getenv("LLM_MODEL", "gpt-5.5")
api_key = os.getenv("LLM_API_KEY")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(usage_id="agent", model=model, api_key=api_key, base_url=base_url)

# ask_user is opt-in: it is not part of the default tool set.
agent = Agent(llm=llm, tools=[Tool(name=AskUserTool.name)])

workspace = tempfile.mkdtemp(prefix="ask_user_demo_")
conversation = Conversation(agent=agent, workspace=workspace)

conversation.send_message(
    "I want to add authentication to a small HTTP API. Before writing any "
    "code, ask me which authentication approach to use (offer JWT and "
    "session cookies) and how the tokens should be stored. Use the ask_user "
    "tool; do not guess."
)
conversation.run()

if (
    conversation.state.execution_status
    != ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
):
    print(
        "The agent did not call ask_user this time "
        f"(status: {conversation.state.execution_status})."
    )
else:
    pending = ConversationState.get_unmatched_actions(conversation.state.events)
    request = pending[0]
    print("Agent is waiting for an answer to request:", request.id)

    # Answer the request: pick JWT, and use the free-text escape hatch to say
    # how tokens should be stored. Answers are keyed by each question's header.
    action = request.action
    assert isinstance(action, AskUserAction)
    headers = [q.header for q in action.questions]
    answer: dict[str, list[dict[str, str | None]]] = {
        headers[0]: [{"option_id": "jwt"}],
    }
    for header in headers[1:]:
        answer[header] = [{"option_id": None, "label": "in an httpOnly cookie"}]

    conversation.send_message(
        json.dumps({"request_id": request.id, "action": "accept", "answers": answer})
    )

    # The answer resolved the pending request and became the tool observation;
    # resume the run.
    conversation.run()
    print("Final status:", conversation.state.execution_status)

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"EXAMPLE_COST: {cost}")
