"""A parallel tool batch in which one call fails validation, saved and reloaded.

Usage: batch_errors.py CONVERSATION_ID

The conversation's first LLM response asked for several tools at once and one
of them failed argument validation, so its log holds the rejected call
(`ActionEvent` with no action) and its `AgentErrorEvent` between the sibling
actions. This program reopens the saved log the way a program resumes one and
reports what the next LLM request is built from: the messages
`events_to_messages` makes, their Chat Completions form, and where the view
lets a condenser cut.
"""

import sys

from sdk_verify import (
    conversation_record,
    emit,
    make_llm,
    open_conversation,
)

from openhands.sdk import LLM
from openhands.sdk.context.view import View
from openhands.sdk.event import ActionEvent, AgentErrorEvent, LLMConvertibleEvent


cid = sys.argv[1]
record = conversation_record(cid)
conversation = open_conversation(record, llm=make_llm([]))
events = list(conversation.state.events)
conversation.close()

convertible = [e for e in events if isinstance(e, LLMConvertibleEvent)]
first_action = next(e for e in convertible if isinstance(e, ActionEvent))
batch_id = first_action.llm_response_id
batch_calls = [
    e.tool_call_id
    for e in convertible
    if isinstance(e, ActionEvent) and e.llm_response_id == batch_id
]
rejected = [
    e.tool_call_id
    for e in convertible
    if isinstance(e, ActionEvent) and e.llm_response_id == batch_id and e.action is None
]
errors = [e.tool_call_id for e in convertible if isinstance(e, AgentErrorEvent)]

messages = LLMConvertibleEvent.events_to_messages(convertible)
chat = LLM(model="gpt-4o-mini", usage_id="format").format_messages_for_llm(messages)

# The assistant message(s) carrying this batch's calls, and what follows them.
batch_assistants = [
    i
    for i, m in enumerate(chat)
    if m["role"] == "assistant"
    and any(c["id"] in batch_calls for c in m.get("tool_calls") or [])
]
first = batch_assistants[0] if batch_assistants else -1
answered: list[str] = []
for m in chat[first + 1 :]:
    if m["role"] != "tool":
        break
    answered.append(m["tool_call_id"])

# Every tool call is answered by a tool message before the next assistant turn.
pending: set[str] = set()
ordered = True
for m in chat:
    if m["role"] == "assistant":
        if pending:
            ordered = False
        pending = {c["id"] for c in m.get("tool_calls") or []}
    elif m["role"] == "tool":
        pending.discard(m["tool_call_id"])
ordered = ordered and not pending

# Where a condenser may cut: no index strictly inside the batch's span.
view = View.from_events(events)
positions = [
    i
    for i, e in enumerate(view.events)
    if isinstance(e, ActionEvent) and e.llm_response_id == batch_id
]
inside = set(range(positions[0] + 1, positions[-1] + 1)) if positions else set()
cuts_inside = sorted(inside & set(view.manipulation_indices))

emit(
    {
        "ok": True,
        "status": str(conversation.state.execution_status.value),
        "kinds": [type(e).__name__ for e in convertible],
        "batch_calls": len(batch_calls),
        "rejected_calls": len(rejected),
        "error_events": len(errors),
        "error_matches_rejected": errors == rejected,
        "batch_assistant_messages": len(batch_assistants),
        "batch_assistant_tool_calls": len(chat[first].get("tool_calls") or [])
        if first >= 0
        else 0,
        "answered_right_after": sorted(answered) == sorted(batch_calls),
        "answered_count": len(answered),
        "every_call_answered_in_order": ordered,
        "chat_roles": [m["role"] for m in chat],
        "view_batch_span": len(inside),
        "view_cuts_inside_batch": len(cuts_inside),
    }
)
