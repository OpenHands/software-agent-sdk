"""Emoji reactions in an SDK program: the agent's `react`/`unreact` tools and
the user's `conversation.react(...)`.

Turn 1 (user: "Please look into the build."): the scripted agent reacts
`eyes` (no message id, so the user's latest message), tries an emoji outside
the set, takes `eyes` back, reacts `white_check_mark` and finishes. The user
then reacts `heart` without a target (it goes on the agent's answer) and tries
an emoji outside the set. Turn 2 (user: "Thanks, that's all."): the agent
finishes. A recording LLM keeps the text each call was sent, so the program
reports whether the agent saw the user's reaction and its own.

With the argument `live` the run's real model gets the two tools and one
message asking it to react first and then answer; the program reports the
reactions it left.
"""

import sys

from sdk_verify import emit, make_llm, run_paths, script_messages

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import ActionEvent, MessageEvent, ReactionEvent
from openhands.sdk.testing import TestLLM
from openhands.tools.reaction import ReactTool, UnreactTool


SEEN: list[str] = []


class RecordingLLM(TestLLM):
    def completion(self, messages, *args, **kwargs):  # type: ignore[override]
        SEEN.append(
            "\n".join(
                getattr(c, "text", "") for m in messages for c in (m.content or [])
            )
        )
        return super().completion(messages, *args, **kwargs)


paths = run_paths()


def live() -> None:
    conversation = Conversation(
        agent=Agent(
            llm=make_llm(),
            tools=[Tool(name=ReactTool.name), Tool(name=UnreactTool.name)],
        ),
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        max_iteration_per_run=6,
        visualizer=None,
    )
    conversation.send_message(
        "First react to this message with the eyes emoji using the react tool."
        " Then answer: what is 6 times 7? Reply with just the number."
    )
    message_id = next(
        e.id
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    )
    conversation.run()
    events = list(conversation.state.events)
    emit(
        {
            "ok": True,
            "execution_status": conversation.state.execution_status.value,
            "agent_reactions": [
                {
                    "emoji": e.emoji,
                    "removed": e.removed,
                    "on_message": e.target_event_id == message_id,
                }
                for e in events
                if isinstance(e, ReactionEvent) and e.source == "agent"
            ],
            "react_calls": sum(
                1
                for e in events
                if isinstance(e, ActionEvent) and e.tool_name == "react"
            ),
        }
    )
    conversation.close()


if sys.argv[1:] == ["live"]:
    live()
    raise SystemExit(0)

FIRST = "Please look into the build."
llm = RecordingLLM.from_messages(
    script_messages(
        [
            {"tool": "react", "args": {"emoji": "eyes"}},
            {"tool": "react", "args": {"emoji": "rocket"}},
            {"tool": "unreact", "args": {"emoji": "eyes"}},
            {"tool": "react", "args": {"emoji": "white_check_mark"}},
            {"finish": "The build is green."},
            {"finish": "You're welcome."},
        ]
    ),
    usage_id="agent",
)
conversation = Conversation(
    agent=Agent(
        llm=llm,
        tools=[Tool(name=ReactTool.name), Tool(name=UnreactTool.name)],
    ),
    workspace=str(paths["workspace"]),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
)
conversation.send_message(FIRST)
first_msg_id = next(
    e.id
    for e in conversation.state.events
    if isinstance(e, MessageEvent) and e.source == "user"
)
conversation.run()
status_1 = conversation.state.execution_status.value

user_reaction = conversation.react("heart")
try:
    conversation.react("rocket")
    bad_user_emoji = "no error"
except ValueError as exc:
    bad_user_emoji = str(exc)[:200]
status_after_react = conversation.state.execution_status.value

conversation.send_message("Thanks, that's all.")
conversation.run()
status_2 = conversation.state.execution_status.value

events = list(conversation.state.events)
finish_ids = [
    e.id for e in events if isinstance(e, ActionEvent) and e.tool_name == "finish"
]
reactions = [
    {
        "source": e.source,
        "emoji": e.emoji,
        "removed": e.removed,
        "on_first_message": e.target_event_id == first_msg_id,
        "on_first_finish": bool(finish_ids) and e.target_event_id == finish_ids[0],
        "text_ending": e.target_text_ending,
    }
    for e in events
    if isinstance(e, ReactionEvent)
]
kinds = [type(e).__name__ for e in events]
agent_reaction_preceded_by = [
    kinds[i - 1]
    for i, e in enumerate(events)
    if isinstance(e, ReactionEvent) and e.source == "agent" and i > 0
]
first_reaction_at = kinds.index("ReactionEvent") if "ReactionEvent" in kinds else -1
observations = [
    {
        "tool": getattr(e, "tool_name", None),
        "is_error": getattr(e.observation, "is_error", None),
        "text": getattr(e.observation, "text", "")[:200],
    }
    for e in events
    if type(e).__name__ == "ObservationEvent"
]
errors = [
    getattr(e, "error", "")[:200]
    for e in events
    if type(e).__name__ == "AgentErrorEvent"
]
turn2_text = SEEN[-1] if SEEN else ""
conversation.close()
emit(
    {
        "ok": True,
        "status_turn1": status_1,
        "status_after_user_react": status_after_react,
        "status_turn2": status_2,
        "reactions": reactions,
        "agent_reaction_preceded_by": agent_reaction_preceded_by,
        "event_before_first_reaction": kinds[first_reaction_at - 1]
        if first_reaction_at > 0
        else None,
        "observations": observations,
        "agent_errors": errors,
        "bad_user_emoji": bad_user_emoji,
        "user_reaction_target_is_finish": bool(finish_ids)
        and user_reaction.target_event_id == finish_ids[0],
        "turn2_sees_user_heart": "[The user reacted \u2764\ufe0f to this message:]\n"
        "The build is green." in turn2_text,
        "turn2_user_reaction_lines": turn2_text.count("[The user reacted"),
        "turn2_sees_own_reaction_line": "[You " in turn2_text,
        "llm_calls": len(SEEN),
    }
)
