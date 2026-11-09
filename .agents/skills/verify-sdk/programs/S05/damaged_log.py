"""Damage one saved event file of a finished conversation and reopen it, as a
program finds it after a crash or a bad copy.

Usage: damaged_log.py CONVERSATION_ID corrupt|delete [INDEX]
"""

import sys

from sdk_verify import (
    conversation_dir,
    conversation_record,
    emit,
    import_tools,
    make_llm,
    open_conversation,
)


cid, mode = sys.argv[1], sys.argv[2]
index = int(sys.argv[3]) if len(sys.argv) > 3 else 3
record = conversation_record(cid)
import_tools(record["tools"])
files = sorted((conversation_dir(cid) / "events").glob("event-*.json"))
before = len(files)
target = files[index]
if mode == "corrupt":
    target.write_text("{not json")
else:
    target.unlink()
try:
    conversation = open_conversation(record, llm=make_llm([]))
    events = list(conversation.state.events)
    # The log reads a damaged file as an error-event stand-in in its place.
    stand_ins = sum(
        1
        for e in events
        if type(e).__name__ == "ConversationErrorEvent"
        and getattr(e, "code", None) == "UnreadableEventFile"
    )
    after = len(events) - stand_ins
    conversation.close()
    error = None
except Exception as exc:
    after = None
    stand_ins = None
    error = f"{type(exc).__name__}: {exc}"[:300]

emit(
    {
        "ok": True,
        "mode": mode,
        "damaged": target.name,
        "events_before": before,
        "events_after": after,
        "stand_ins": stand_ins,
        "lost_beyond_damaged": None if after is None else before - 1 - after,
        "reopen_error": error,
    }
)
