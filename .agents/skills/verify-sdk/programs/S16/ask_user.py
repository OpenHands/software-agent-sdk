"""The `ask_user` tool in an SDK program: the agent asks one question with
options, the turn ends, and the program's user answers it.

Usage: ask_user.py [scripted|live]

Scripted (TestLLM through the real agent loop, one conversation per scenario):
  ask        the agent asks; the call ends the turn, the question is pending,
             and survives reloading the persisted conversation
  answer     wrong id, missing option, empty answer, then a tap (option and
             words) recorded as an AskUserAnswerEvent plus the user message
             the agent reads; the next run answers; a second tap is refused
  labels     two options with the same label: the answer names the option
  chat       a relayed message (with a sender) does not close the question; a
             message of the user's own does
  invalid    one option, an out-of-range recommendation, a blank label, and two
             questions in one response
  confirm    AlwaysConfirm: asking needs no confirmation
  stop-hook  a Stop hook that denies completion once: it does not run over the
             question (sync and async), and runs once the user has answered
  refine     ask_user and finish in one response with iterative refinement:
             the question wins, no refinement message is sent
In `live` mode the run's real model gets only `ask_user` and a request to ask.
"""

import asyncio
import sys

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.critic import CriticBase, CriticResult, IterativeRefinementConfig
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.ask_user import find_pending_ask
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.tools.ask_user import AskUserTool


MODE = sys.argv[1] if len(sys.argv) > 1 else "scripted"
paths = run_paths()
WS = str(paths["workspace"])
QUESTION = {
    "question": "Delete the old build caches?",
    "options": [
        {"label": "Delete them", "consequence": "Frees 4 GB; cannot be undone."},
        {"label": "Keep them", "consequence": "Nothing changes."},
    ],
    "recommended": 1,
}
out: dict = {"ok": True, "mode": MODE}


def ask(**overrides) -> dict:
    return {"tool": "ask_user", "args": {**QUESTION, **overrides}}


def conversation_for(script, *, llm=None, **kw):
    kw.setdefault("visualizer", None)
    critic = kw.pop("critic", None)
    return Conversation(
        agent=Agent(
            llm=llm or make_llm(script),
            tools=[Tool(name=AskUserTool.name)],
            critic=critic,
        ),
        workspace=WS,
        **kw,
    )


def pending(conversation):
    with conversation.state:
        return find_pending_ask(conversation.state.active_branch())


def observations(conversation) -> list[dict]:
    return [
        {
            "tool": e.tool_name,
            "is_error": e.observation.is_error,
            "text": getattr(e.observation, "text", "")[:200],
        }
        for e in conversation.state.events
        if isinstance(e, ObservationEvent)
    ]


def kinds(conversation) -> list[str]:
    return [type(e).__name__ for e in conversation.state.events]


def attempt(fn):
    try:
        fn()
        return "no error"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:300]


def live() -> None:
    conversation = conversation_for(None, llm=make_llm(), max_iteration_per_run=4)
    conversation.send_message(
        "I want a demo file in the workspace. Ask me whether to name it qa-a.txt"
        " or qa-b.txt, then stop until I answer."
    )
    conversation.run()
    p = pending(conversation)
    emit(
        {
            "ok": True,
            "execution_status": conversation.state.execution_status.value,
            "ask_calls": sum(
                1
                for e in conversation.state.events
                if isinstance(e, ActionEvent) and e.tool_name == "ask_user"
            ),
            "pending": p is not None,
            "options": len(p.option_labels) if p else 0,
        }
    )
    conversation.close()


if MODE == "live":
    live()
    raise SystemExit(0)


# ask: the call ends the turn and the question is pending, also after a reload
llm = make_llm([ask(), {"finish": "should never be reached"}])
conversation = conversation_for(
    None, llm=llm, persistence_dir=str(paths["conversations"])
)
conversation.send_message("Clean up the build machine.")
conversation.run()
p = pending(conversation)
cid = conversation.id
action = next(e for e in conversation.state.events if isinstance(e, ActionEvent))
out["ask"] = {
    "status": conversation.state.execution_status.value,
    "llm_calls": llm.call_count,
    "tool": action.tool_name,
    "action_kind": type(action.action).__name__,
    "question": action.action.question,
    "recommended": action.action.recommended,
    "observations": observations(conversation),
    "pending_id_is_action": p is not None and p.ask_id == action.id,
    "pending_labels": list(p.option_labels) if p else [],
    "pending_recommended": p.recommended if p else None,
}
conversation.close()
reloaded = conversation_for(
    [], persistence_dir=str(paths["conversations"]), conversation_id=cid
)
rp = pending(reloaded)
out["ask"]["pending_after_reload"] = rp is not None and rp.ask_id == action.id
reloaded.close()

# answer: refusals, a tap, the next turn, a second tap
llm = make_llm([ask(), {"finish": "Kept the caches."}])
conversation = conversation_for(None, llm=llm)
conversation.send_message("Clean up the build machine.")
conversation.run()
ask_id = pending(conversation).ask_id
answer = {
    "wrong_id": attempt(lambda: conversation.answer_ask("not-the-question", 0)),
    "missing_option": attempt(lambda: conversation.answer_ask(ask_id, 2)),
    "empty": attempt(lambda: conversation.answer_ask(ask_id, None, "   ")),
    "events_after_refusals": kinds(conversation).count("AskUserAnswerEvent"),
}
event, message = conversation.answer_ask(ask_id, 1, "  keep them for a week  ")
answer.update(
    {
        "event_kind": type(event).__name__,
        "event_source": event.source,
        "event_ask_id_matches": event.ask_id == ask_id,
        "option_index": event.option_index,
        "option_label": event.option_label,
        "text": event.text,
        "event_parent_is_observation": event.parent_id
        == next(
            e.id for e in conversation.state.events if isinstance(e, ObservationEvent)
        ),
        "still_pending_before_message": pending(conversation) is not None,
        "message_text": message.content[0].text,
        "status_before_send": conversation.state.execution_status.value,
    }
)
conversation.send_message(message)
answer["pending_after_message"] = pending(conversation) is not None
conversation.run()
answer.update(
    {
        "status": conversation.state.execution_status.value,
        "llm_calls": llm.call_count,
        "second_tap": attempt(lambda: conversation.answer_ask(ask_id, 0)),
        "kinds_tail": kinds(conversation)[-5:],
    }
)
out["answer"] = answer
conversation.close()

# labels: equal labels stay distinguishable
same = [{"label": "Default"}, {"label": "Default"}]
conversation = conversation_for([ask(options=same, recommended=None)])
conversation.send_message("Pick the profile.")
conversation.run()
_, message = conversation.answer_ask(pending(conversation).ask_id, 1)
out["labels"] = {"message_text": message.content[0].text}
conversation.close()

# chat: a relayed message does not answer; the user's own words do
llm = make_llm([ask(), {"finish": "Deleting them."}])
conversation = conversation_for(None, llm=llm)
conversation.send_message("Clean up the build machine.")
conversation.run()
conversation.send_message(
    Message(role="user", content=[TextContent(text="Delete them.")]),
    sender="other-conversation",
)
relayed_closes = pending(conversation) is None
conversation.send_message("Delete them, please.")
out["chat"] = {
    "relayed_closes": relayed_closes,
    "own_message_closes": pending(conversation) is None,
}
conversation.run()
out["chat"]["status"] = conversation.state.execution_status.value
out["chat"]["llm_calls"] = llm.call_count
conversation.close()

# invalid questions
invalid: dict = {}
for name, step in [
    ("one_option", ask(options=[{"label": "Only"}], recommended=None)),
    ("bad_recommended", ask(recommended=5)),
    ("blank_label", ask(options=[{"label": "  "}, {"label": "Keep"}])),
]:
    llm = make_llm([step, {"finish": "Asked nothing."}])
    conversation = conversation_for(None, llm=llm)
    conversation.send_message("Clean up the build machine.")
    conversation.run()
    errors = [
        getattr(e, "error", "")[:200]
        for e in conversation.state.events
        if type(e).__name__ == "AgentErrorEvent"
    ]
    invalid[name] = {
        "pending": pending(conversation) is not None,
        "llm_calls": llm.call_count,
        "status": conversation.state.execution_status.value,
        "agent_errors": errors,
        "observations": observations(conversation),
    }
    conversation.close()
two = {
    "tools": [
        {"tool": "ask_user", "args": QUESTION},
        {"tool": "ask_user", "args": {**QUESTION, "question": "Which region?"}},
    ]
}
llm = make_llm([two, {"finish": "never"}])
conversation = conversation_for(None, llm=llm)
conversation.send_message("Clean up the build machine.")
conversation.run()
p = pending(conversation)
invalid["two_questions"] = {
    "observations": observations(conversation),
    "pending_question": p.question if p else None,
    "llm_calls": llm.call_count,
    "status": conversation.state.execution_status.value,
}
conversation.close()
out["invalid"] = invalid

# confirm: AlwaysConfirm does not stop on a question
conversation = conversation_for([ask(), {"finish": "never"}])
conversation.set_confirmation_policy(AlwaysConfirm())
conversation.send_message("Clean up the build machine.")
conversation.run()
out["confirm"] = {
    "status": conversation.state.execution_status.value,
    "pending": pending(conversation) is not None,
}
conversation.close()


# stop-hook: a Stop hook that denies completion the first time it runs
def stop_hook_case(use_async: bool) -> dict:
    marker = paths["run"] / f"stop-hook-{'async' if use_async else 'sync'}"
    command = (
        f"test -f {marker} || {{ touch {marker}; echo 'Keep working' >&2; exit 2; }}"
    )
    llm = make_llm([ask(), {"text": "Kept them."}, {"text": "Checked: kept them."}])
    conversation = conversation_for(
        None,
        llm=llm,
        hook_config=HookConfig(
            stop=[HookMatcher(hooks=[HookDefinition(command=command)])]
        ),
    )
    go = (lambda: asyncio.run(conversation.arun())) if use_async else conversation.run
    conversation.send_message("Clean up the build machine.")
    go()
    result = {
        "status_after_ask": conversation.state.execution_status.value,
        "llm_calls_after_ask": llm.call_count,
        "pending_after_ask": pending(conversation) is not None,
        "hook_ran_over_question": marker.exists(),
    }
    conversation.send_message("Keep them.")
    go()
    result.update(
        {
            "status_after_answer": conversation.state.execution_status.value,
            "llm_calls_after_answer": llm.call_count,
            "hook_ran_after_answer": marker.exists(),
            "hook_feedback_messages": sum(
                1
                for e in conversation.state.events
                if isinstance(e, MessageEvent) and "Keep working" in str(e.llm_message)
            ),
        }
    )
    conversation.close()
    return result


out["stop_hook"] = {"sync": stop_hook_case(False), "async": stop_hook_case(True)}


# refine: ask_user and finish in one response, refinement on
class LowCritic(CriticBase):
    def evaluate(self, events, git_patch=None) -> CriticResult:  # noqa: ARG002
        return CriticResult(score=0.1, message="not done")


mixed = {
    "tools": [
        {"tool": "ask_user", "args": QUESTION},
        {"tool": "finish", "args": {"message": "Done"}},
    ]
}
llm = make_llm([mixed, {"text": "Premature refinement"}])
conversation = conversation_for(
    None,
    llm=llm,
    critic=LowCritic(iterative_refinement=IterativeRefinementConfig(max_iterations=1)),
)
conversation.send_message("Clean up the build machine.")
conversation.run()
out["refine"] = {
    "status": conversation.state.execution_status.value,
    "pending": pending(conversation) is not None,
    "llm_calls": llm.call_count,
    "user_messages": sum(
        1
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    ),
}
conversation.close()

emit(out)
