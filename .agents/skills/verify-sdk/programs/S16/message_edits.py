"""The agent edits its earlier messages with `edit_message`.

Turn 1 (user: "Check the build."): the scripted agent asks a question in a
plain message. Turn 2 (user: "Yes, windy."): it strikes the question with
`edit_message`, tries to edit the user's text, sends two edits of the same
message in one response, and finishes. Turn 3 (user: "One test was flaky."):
it edits its earlier `finish` and finishes again. The user then reacts to that
first `finish`. A recording LLM keeps what each call was sent, so the program
reports how the edited message reads to the LLM; it also converts the events
with `events_to_messages`, reads each message back with `message_text`, and
reloads the persisted conversation.

With the argument `live` the run's real model gets the tool and two turns:
it asks a question, and after the answer is told to strike it.
"""

import sys

from sdk_verify import emit, make_llm, run_paths, script_messages

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import (
    ActionEvent,
    Event,
    LLMConvertibleEvent,
    MessageEditEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.event.message_edit import message_text
from openhands.sdk.testing import TestLLM
from openhands.tools.message_edit import EditMessageTool


SEEN: list[list[dict]] = []


def _texts(message) -> str:
    return "\n".join(getattr(c, "text", "") for c in (message.content or []))


class RecordingLLM(TestLLM):
    def completion(self, messages, *args, **kwargs):  # type: ignore[override]
        SEEN.append([{"role": m.role, "text": _texts(m)} for m in messages])
        return super().completion(messages, *args, **kwargs)


paths = run_paths()
QUESTION = "Should I run the lookup on windy?"
STRUCK = "~~Should I run the lookup on windy?~~ [Edit: Running it on windy.]"


def live() -> None:
    conversation = Conversation(
        agent=Agent(llm=make_llm(), tools=[Tool(name=EditMessageTool.name)]),
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        max_iteration_per_run=6,
        visualizer=None,
    )
    conversation.send_message(
        f"Reply with exactly this question and nothing else, without tools: {QUESTION}"
    )
    conversation.run()
    conversation.send_message(
        "Yes, run it on windy. Now use the edit_message tool on your question: "
        f'old_str "{QUESTION}", new_str "{STRUCK}". Then answer: done.'
    )
    conversation.run()
    events = list(conversation.state.events)
    edits = [e for e in events if isinstance(e, MessageEditEvent)]
    agent_messages = {
        e.id
        for e in events
        if (isinstance(e, MessageEvent) and e.source == "agent")
        or (isinstance(e, ActionEvent) and e.tool_name == "finish")
    }
    emit(
        {
            "ok": True,
            "execution_status": conversation.state.execution_status.value,
            "edit_calls": sum(
                1
                for e in events
                if isinstance(e, ActionEvent) and e.tool_name == "edit_message"
            ),
            "edits": len(edits),
            "edits_target_agent_messages": all(
                e.target_event_id in agent_messages for e in edits
            ),
            "edited_text_has_strike": any("~~" in e.text for e in edits),
        }
    )
    conversation.close()


if sys.argv[1:] == ["live"]:
    live()
    raise SystemExit(0)

llm = RecordingLLM.from_messages(
    script_messages(
        [
            {"text": QUESTION},
            {
                "tool": "edit_message",
                "args": {
                    "old_str": "Should I run the lookup on windy?",
                    "new_str": STRUCK,
                },
            },
            {
                "tool": "edit_message",
                "args": {"old_str": "Yes, windy", "new_str": "No"},
            },
            {
                "tools": [
                    {
                        "tool": "edit_message",
                        "args": {"old_str": "Running it", "new_str": "Ran it"},
                    },
                    {
                        "tool": "edit_message",
                        "args": {"old_str": "[Edit: ", "new_str": "[Note: "},
                    },
                ]
            },
            {"finish": "The build is green."},
            {
                "tool": "edit_message",
                "args": {
                    "old_str": "The build is green.",
                    "new_str": "~~The build is green.~~ [Edit: one test is flaky.]",
                },
            },
            {"finish": "Noted."},
        ]
    ),
    usage_id="agent",
)
conversation = Conversation(
    agent=Agent(llm=llm, tools=[Tool(name=EditMessageTool.name)]),
    workspace=str(paths["workspace"]),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
)
statuses = []
for text in ("Check the build.", "Yes, windy.", "One test was flaky."):
    conversation.send_message(text)
    conversation.run()
    statuses.append(conversation.state.execution_status.value)

events = list(conversation.state.events)
kinds = [type(e).__name__ for e in events]
question = next(
    e for e in events if isinstance(e, MessageEvent) and e.source == "agent"
)
finishes = [e for e in events if isinstance(e, ActionEvent) and e.tool_name == "finish"]
edits = [e for e in events if isinstance(e, MessageEditEvent)]
edit_obs = [
    e
    for e in events
    if isinstance(e, ObservationEvent) and e.tool_name == "edit_message"
]

reaction = conversation.react("heart", target_event_id=finishes[0].id)

# What the LLM is given: the last call, and events_to_messages over the log.
last_call = SEEN[-1] if SEEN else []
assistant_texts = [m["text"] for m in last_call if m["role"] == "assistant"]
tool_texts = [m["text"] for m in last_call if m["role"] == "tool"]
converted = LLMConvertibleEvent.events_to_messages(
    [e for e in events if isinstance(e, LLMConvertibleEvent)]
)
converted_assistant = [_texts(m) for m in converted if m.role == "assistant"]
view_kinds = [type(e).__name__ for e in conversation.state.view.events]

cid = conversation.id
conversation.close()
reloaded = Conversation(
    agent=Agent(llm=TestLLM.from_messages([]), tools=[Tool(name=EditMessageTool.name)]),
    conversation_id=cid,
    workspace=str(paths["workspace"]),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
)
reloaded_edits = [e for e in reloaded.state.events if isinstance(e, MessageEditEvent)]
reloaded_question_text = message_text(
    reloaded.state.walk_back(until=question.id), question
)
reloaded.close()

emit(
    {
        "ok": True,
        "statuses": statuses,
        "edits": [
            {
                "source": e.source,
                "on_question": e.target_event_id == question.id,
                "on_first_finish": e.target_event_id == finishes[0].id,
                "text": e.text,
                "after": kinds[events.index(e) - 1],
            }
            for e in edits
        ],
        "observations": [
            {"is_error": e.observation.is_error, "text": e.observation.text[:300]}
            for e in edit_obs
        ],
        "question_reads": message_text(reversed(events), question),
        "question_written": question.llm_message.content[0].text,
        "first_finish_reads": message_text(reversed(events), finishes[0]),
        "reaction_text_ending": reaction.target_text_ending,
        "edit_in_view": "MessageEditEvent" in view_kinds,
        "edit_is_llm_convertible": isinstance(edits[0], LLMConvertibleEvent)
        if edits
        else None,
        "llm_assistant_has_question": QUESTION in assistant_texts,
        "llm_assistant_has_struck_text": any(STRUCK in t for t in assistant_texts),
        "llm_sees_edit_results": sum(
            "Edited. The message now reads:" in t for t in tool_texts
        ),
        "converted_assistant_has_question": QUESTION in converted_assistant,
        "converted_has_struck_text": any(STRUCK in t for t in converted_assistant),
        "roundtrip_equal": bool(edits)
        and Event.model_validate_json(edits[0].model_dump_json()) == edits[0],
        "reloaded_edits": len(reloaded_edits),
        "reloaded_question_reads": reloaded_question_text,
        "llm_calls": len(SEEN),
    }
)
