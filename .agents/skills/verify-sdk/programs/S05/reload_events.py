"""Reload a finished conversation's events the way a program would.

Usage: reload_events.py CONVERSATION_ID

Reads the persisted event files, restores each with Event.model_validate_json,
turns the LLM-visible events back into messages and OpenAI payloads, and
walks the event tree from the reopened conversation.
"""

import re
import sys

from sdk_verify import (
    conversation_dir,
    conversation_record,
    emit,
    import_tools,
    make_llm,
    open_conversation,
)

from openhands.sdk import LLM
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent, Event, LLMConvertibleEvent, MessageEvent
from openhands.sdk.event.resume_transcript import render_resume_transcript


cid = sys.argv[1]
record = conversation_record(cid)
import_tools(record["tools"])
files = sorted((conversation_dir(cid) / "events").glob("event-*.json"))
names_ok = all(re.fullmatch(r"event-\d{5,}-[\w-]+\.json", f.name) for f in files)
restored = [Event.model_validate_json(f.read_text()) for f in files]
roundtrip = all(
    Event.model_validate_json(e.model_dump_json()) == e for e in restored
) and all(f.name.endswith(f"-{e.id}.json") for f, e in zip(files, restored))

# The reopened conversation sees the same log, in order, from disk.
conversation = open_conversation(record, llm=make_llm([]))
events = list(conversation.state.events)
same_log = [e.id for e in events] == [e.id for e in restored]
leaf = events[-1].id
chain = conversation.state.events.path_to_root(leaf)
again = list(conversation.state.events)
cached = all(a is b for a, b in zip(events, again))

# Events are immutable records.
try:
    events[0].source = "user"  # type: ignore[misc]
    frozen = False
except Exception:
    frozen = True
try:
    MessageEvent.model_validate({**events[1].model_dump(), "unexpected": 1})
    extra_forbidden = False
except Exception:
    extra_forbidden = True
try:
    MessageEvent(id="__root__", **{k: v for k, v in events[1] if k != "id"})
    root_id_rejected = False
except Exception:
    root_id_rejected = True

# LLM-visible events back to messages and provider payloads.
convertible = [e for e in events if isinstance(e, LLMConvertibleEvent)]
messages = LLMConvertibleEvent.events_to_messages(convertible)
actions = [e for e in events if isinstance(e, ActionEvent)]
batches = {a.llm_response_id for a in actions}
chat = LLM(model="gpt-4o-mini", usage_id="format").format_messages_for_llm(messages)
responses_llm = LLM(model="openai/gpt-5-mini", usage_id="format-responses")
instructions, items = responses_llm.format_messages_for_responses(messages)
transcript = render_resume_transcript(events) or ""
conversation.close()

emit(
    {
        "ok": True,
        "event_files": len(files),
        "file_names_ok": names_ok,
        "roundtrip_equal": roundtrip,
        "kinds": [type(e).__name__ for e in restored],
        "reopened_same_log": same_log,
        "path_to_root": len(chain),
        "iteration_cached": cached,
        "frozen": frozen,
        "extra_forbidden": extra_forbidden,
        "root_id_rejected": root_id_rejected,
        "message_roles": [m.role for m in messages],
        "action_events": len(actions),
        "llm_responses_with_actions": len(batches),
        "assistant_messages_with_tool_calls": sum(
            1 for m in messages if m.role == "assistant" and m.tool_calls
        ),
        "chat_roles": [m["role"] for m in chat],
        "responses_instructions": bool(instructions),
        "responses_item_types": sorted({i.get("type", "message") for i in items}),
        "unmatched_actions": len(ConversationState.get_unmatched_actions(events)),
        "transcript_mentions_command": "notes.txt" in transcript,
        "visualize_empty_kinds": sorted(
            {type(e).__name__ for e in events if not str(e.visualize).strip()}
        ),
    }
)
