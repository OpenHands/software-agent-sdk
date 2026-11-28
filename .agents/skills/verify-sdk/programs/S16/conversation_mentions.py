"""Conversation mentions in an SDK program, without an agent server.

`conversation_mention(title, id)` builds the `[Title](conversation:<hex>)`
link. The `find_conversations` and `read_conversation` tools read an agent
server's REST API (`OH_INTERNAL_SERVER_URL`, default `http://127.0.0.1:8000`);
here that variable points at a closed local port, so the scripted agent's calls
show what a program gets with no server. A recording LLM keeps the system
prompt, to report the tools' shared guidance.
"""

import os
import socket
import uuid

from sdk_verify import emit, run_paths, script_messages

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.conversation.types import (
    CONVERSATION_MENTION_SCHEME,
    conversation_mention,
)
from openhands.sdk.event import ObservationEvent
from openhands.sdk.testing import TestLLM
from openhands.tools.conversation_mentions import (
    FindConversationsTool,
    ReadConversationTool,
)


SEEN: list[str] = []


class RecordingLLM(TestLLM):
    def completion(self, messages, *args, **kwargs):  # type: ignore[override]
        SEEN.append(
            "\n".join(
                getattr(c, "text", "") for m in messages for c in (m.content or [])
            )
        )
        return super().completion(messages, *args, **kwargs)


with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
os.environ["OH_INTERNAL_SERVER_URL"] = f"http://127.0.0.1:{closed_port}"

cid = uuid.UUID("12345678-1234-5678-1234-567812345678")
links = {
    "plain": conversation_mention("Fix the export button", cid),
    "dashed_str": conversation_mention("Fix", str(cid)),
    "hostile": conversation_mention('a](evil) "b" [c] \\ `d`\nnext', cid),
    "blank": conversation_mention("   ", cid),
    "long_len": len(conversation_mention("x" * 200, cid)),
}

paths = run_paths()
tools = [Tool(name=FindConversationsTool.name), Tool(name=ReadConversationTool.name)]
llm = RecordingLLM.from_messages(
    script_messages(
        [
            {"tool": "find_conversations", "args": {"query": "export"}},
            {"tool": "read_conversation", "args": {"conversation": links["plain"]}},
            {"tool": "read_conversation", "args": {"conversation": "the export one"}},
            {"finish": "done"},
        ]
    ),
    usage_id="agent",
)
conversation = Conversation(
    agent=Agent(llm=llm, tools=tools),
    workspace=str(paths["workspace"]),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
)
conversation.send_message("Which conversation fixed the export button?")
conversation.run()
observations = [
    {
        "tool": e.tool_name,
        "is_error": e.observation.is_error,
        "text": e.observation.text[:300],
    }
    for e in conversation.state.events
    if isinstance(e, ObservationEvent)
]
annotations = {
    name: tool.annotations.readOnlyHint if tool.annotations else None
    for name, tool in conversation.agent.tools_map.items()
    if name in ("find_conversations", "read_conversation")
}
status = conversation.state.execution_status.value
conversation.close()

try:
    Conversation(
        agent=Agent(
            llm=TestLLM.from_messages([]),
            tools=[Tool(name=FindConversationsTool.name, params={"limit": 5})],
        ),
        workspace=str(paths["workspace"]),
        visualizer=None,
    ).send_message("hi")
    params_error = "no error"
except Exception as exc:  # noqa: BLE001
    params_error = f"{type(exc).__name__}: {exc}"[:300]

emit(
    {
        "ok": True,
        "scheme": CONVERSATION_MENTION_SCHEME,
        "links": links,
        "execution_status": status,
        "observations": observations,
        "read_only": annotations,
        "system_prompt_has_guidance": bool(SEEN)
        and "[Title](conversation:<id>)" in SEEN[0],
        "params_error": params_error,
    }
)
