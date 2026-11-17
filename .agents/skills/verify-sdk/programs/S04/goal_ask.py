"""`run_goal` and the agent's `ask_user` questions.

Usage: goal_ask.py MODE
  asked   the agent asks during a goal round: run_goal returns GoalAsked
          without judging; called again while the question is pending it
          returns at once and sends nothing; once the user answers, it runs
          and the judge says complete
  branch  the agent asks, the program navigates back to the user message
          before the question and runs a new objective: only the active branch
          counts, so the objective is sent and run (#6907)

Scripted throughout: the agent and the judge are separate TestLLMs, so the
program reports how many calls each got.
"""

import sys

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.conversation.goal import GoalAsked, run_goal
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.ask_user import find_pending_ask
from openhands.tools.ask_user import AskUserTool


MODE = sys.argv[1]
paths = run_paths()
ASK = {
    "tool": "ask_user",
    "args": {
        "question": "Delete the old build caches?",
        "options": [{"label": "Delete them"}, {"label": "Keep them"}],
        "recommended": 1,
    },
}
COMPLETE = '{"score": 1.0, "complete": true, "missing": ""}'


def conversation_for(llm):
    return Conversation(
        agent=Agent(llm=llm, tools=[Tool(name=AskUserTool.name)]),
        workspace=str(paths["workspace"]),
        visualizer=None,
    )


def pending(conversation):
    with conversation.state:
        return find_pending_ask(conversation.state.active_branch())


def user_messages(conversation, *, active: bool = False) -> list[dict]:
    with conversation.state:
        events = (
            conversation.state.active_branch()
            if active
            else list(conversation.state.events)
        )
    return [
        {"text": e.llm_message.content[0].text[:120], "sender": e.sender}
        for e in events
        if isinstance(e, MessageEvent) and e.source == "user"
    ]


def outcome_of(result) -> dict:
    if isinstance(result, GoalAsked):
        return {"kind": "GoalAsked", "ask_id": result.ask_id, "question": result.question}
    return {"kind": type(result).__name__, "status": result.status}


out: dict = {"ok": True, "mode": MODE}

if MODE == "asked":
    agent_llm = make_llm([ASK, {"finish": "Kept the caches."}])
    judge = make_llm([{"text": COMPLETE}], usage_id="judge")
    conversation = conversation_for(agent_llm)
    first = run_goal(conversation, "Tidy the build machine.", judge, max_iterations=3)
    p = pending(conversation)
    out["first"] = outcome_of(first) | {
        "ask_id_is_pending": p is not None and first.ask_id == p.ask_id,
        "agent_calls": agent_llm.call_count,
        "judge_calls": judge.call_count,
        "user_messages": user_messages(conversation),
    }
    again = run_goal(conversation, "Tidy the build machine.", judge, max_iterations=3)
    out["again"] = outcome_of(again) | {
        "same_question": isinstance(again, GoalAsked) and again.ask_id == first.ask_id,
        "agent_calls": agent_llm.call_count,
        "judge_calls": judge.call_count,
        "user_messages": len(user_messages(conversation)),
    }
    _, message = conversation.answer_ask(p.ask_id, 1)
    conversation.send_message(message)
    answered = run_goal(conversation, "Tidy the build machine.", judge, max_iterations=3)
    out["answered"] = outcome_of(answered) | {
        "agent_calls": agent_llm.call_count,
        "judge_calls": judge.call_count,
        "pending": pending(conversation) is not None,
    }
    conversation.close()
elif MODE == "branch":
    agent_llm = make_llm([ASK, {"text": "Completed the new objective."}])
    judge = make_llm([{"text": COMPLETE}], usage_id="judge")
    conversation = conversation_for(agent_llm)
    conversation.send_message("Clean up the build machine.")
    conversation.run()
    asked = pending(conversation)
    first_user = next(
        e.id
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    )
    conversation.navigate_to(first_user)
    out["pending_on_active_branch"] = pending(conversation) is not None
    out["asked_before_navigation"] = asked is not None
    result = run_goal(conversation, "New objective: say done.", judge, max_iterations=2)
    out["outcome"] = outcome_of(result) | {
        "agent_calls": agent_llm.call_count,
        "judge_calls": judge.call_count,
        "objective_sent": any(
            m["text"] == "New objective: say done." and m["sender"] == "goal"
            for m in user_messages(conversation, active=True)
        ),
    }
    conversation.close()
else:
    raise SystemExit(f"unknown mode {MODE!r}")

emit(out)
