"""Compare the cacheable prefixes of normal and side-question Anthropic requests."""

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, run_paths

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool


def message_blocks(body: dict) -> list:
    # A new breakpoint can move onto a later block within the same user message.
    return [
        (
            index,
            message["role"],
            {k: v for k, v in block.items() if k != "cache_control"},
        )
        for index, message in enumerate(body["messages"])
        for block in message["content"]
    ]


workspace = run_paths()["workspace"]
note = workspace / "note.txt"
suffix = "Use the checklist."
question = "Which file was written?"
script = [
    {
        "thinking": "I will create the requested note.",
        "tool": "file_editor",
        "args": {"command": "create", "path": str(note), "file_text": "hello"},
    },
    {"text": "Created the note."},
    {"text": "The side answer."},
    {"text": "Reviewed the note."},
]
with FakeProvider(script) as server:
    llm = LLM(
        model="anthropic/claude-haiku-4-5-20251001",
        api_key=SecretStr("test-key"),
        base_url=server.root,
        num_retries=1,
    )
    conversation = Conversation(
        agent=Agent(
            llm=llm,
            tools=[Tool(name=FileEditorTool.name)],
            agent_context=AgentContext(user_message_suffix=suffix),
        ),
        workspace=str(workspace),
        visualizer=None,
    )
    try:
        conversation.send_message("Write a note.")
        conversation.run()
        conversation.send_message("Review the note.")
        answer = conversation.ask_agent(question)
        conversation.run()
    finally:
        conversation.close()

requests = [request["body"] for request in server.requests]
first = requests[0]
first_blocks, tool_blocks, ask_blocks, continued_blocks = map(message_blocks, requests)
last_blocks = [body["messages"][-1]["content"][-1] for body in requests]
emit(
    {
        "file_written": note.read_text() == "hello",
        "answer": answer,
        "changed_prefix_fields": [
            key
            for key in ("tools", "system", "thinking", "tool_choice")
            if any(body.get(key) != first.get(key) for body in requests[1:])
        ],
        "missing_trailing_breakpoint": [
            turn
            for turn, block in zip(
                ("initial", "tool-result", "ask", "continued"), last_blocks, strict=True
            )
            if block.get("cache_control", {}).get("type") != "ephemeral"
        ],
        "user_prefix_replayed": last_blocks[0].get("text") == suffix
        and tool_blocks[: len(first_blocks)] == first_blocks,
        "tool_prefix_replayed": last_blocks[1]["type"] == "tool_result"
        and continued_blocks[: len(tool_blocks)] == tool_blocks,
        "ask_preserves_continued_prefix": ask_blocks[: len(continued_blocks)]
        == continued_blocks,
        "question_in_ask_tail": any(
            question in block.get("text", "")
            for _, _, block in ask_blocks[len(continued_blocks) :]
        ),
        "side_exchange_in_continuation": any(
            question in block.get("text", "") or answer in block.get("text", "")
            for _, _, block in continued_blocks
        ),
    }
)
