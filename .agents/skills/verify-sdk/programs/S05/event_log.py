"""Append to a conversation's event log from several threads, as two parts of
one program (or two processes sharing the store) would.

Usage: event_log.py CONVERSATION_ID
"""

import sys
import threading

from sdk_verify import (
    conversation_record,
    emit,
    import_tools,
    make_llm,
    open_conversation,
)

from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent


cid = sys.argv[1]
record = conversation_record(cid)
import_tools(record["tools"])


def note(text: str) -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


conversation = open_conversation(record, llm=make_llm([]))
log = conversation.state.events
start = len(log)
indexes: list[int] = []
lock = threading.Lock()


def writer(n: int) -> None:
    for i in range(10):
        idx = log.append(note(f"thread {n} note {i}"))
        with lock:
            indexes.append(idx)


threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
consecutive = sorted(indexes) == list(range(start, start + 40))
duplicate = log[start]
try:
    log.append(duplicate)
    duplicate_rejected = False
except Exception:
    duplicate_rejected = True
negative_index_ok = log[-1].id == log.get_id(len(log) - 1)
lookup_ok = log.get_index(duplicate.id) == start
conversation.close()

emit(
    {
        "ok": True,
        "appended": len(indexes),
        "indexes_consecutive": consecutive,
        "duplicate_rejected": duplicate_rejected,
        "negative_index_ok": negative_index_ok,
        "lookup_ok": lookup_ok,
        "events": len(log),
    }
)
