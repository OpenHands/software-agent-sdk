"""Save notes, reset context, and retrieve original history.

Run without an API key: uv run examples/01_standalone_sdk/notes_retrieval/main.py --offline
For a live run, set LLM_API_KEY and LLM_MODEL; LLM_BASE_URL is optional.
"""  # noqa: E501

import argparse
import json
import os
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LocalConversation, StorageSafetyConfig
from openhands.sdk.context.condenser import NotesRetrievalCondenser
from openhands.sdk.event import HistoryIndexEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins import ContextNotesAction


def tool_message(name: str, arguments: dict) -> Message:
    return Message(
        role="assistant",
        tool_calls=[
            MessageToolCall(
                id=f"call_{name}",
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def offline_llm() -> TestLLM:
    return TestLLM.from_messages(
        [
            tool_message(
                "context_notes",
                {"command": "write", "content": "Launch code: ZX-4916. Region: east."},
            ),
            Message(role="assistant", content=[TextContent(text="Progress saved.")]),
            tool_message("new_context", {}),
            tool_message("context_notes", {"command": "read"}),
            tool_message(
                "conversation_history", {"command": "search", "query": "launch code"}
            ),
            Message(
                role="assistant",
                content=[
                    TextContent(text="The launch code is ZX-4916, in region east.")
                ],
            ),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Use scripted TestLLM.")
    args = parser.parse_args()
    llm = (
        offline_llm()
        if args.offline
        else LLM(
            model=os.environ["LLM_MODEL"],
            api_key=SecretStr(os.environ["LLM_API_KEY"]),
            base_url=os.environ.get("LLM_BASE_URL"),
            usage_id="agent",
        )
    )
    agent = Agent(
        llm=llm,
        tools=[],
        include_default_tools=[
            "FinishTool",
            "ContextNotesTool",
            "ConversationHistoryTool",
            "NewContextTool",
        ],
        condenser=NotesRetrievalCondenser(max_size=30, keep_first=1, keep_recent=2),
    )
    with TemporaryDirectory(prefix="notes-retrieval-") as directory:
        root = Path(directory)
        conversation = LocalConversation(
            agent=agent,
            workspace=root,
            persistence_dir=root / "conversations",
            storage_safety=StorageSafetyConfig(min_free_ratio=0.05),
            max_iteration_per_run=10,
        )
        try:
            conversation.send_message(
                "The launch code is ZX-4916 and the region is east. Save these facts "
                "in context_notes, then acknowledge briefly."
            )
            conversation.run()
            conversation.send_message(
                "Call new_context to reset your context. Then read context_notes, "
                "search conversation_history for 'launch code', and report the "
                "original code and region."
            )
            conversation.run()
            events = conversation.state.active_branch()
            resets = sum(isinstance(event, HistoryIndexEvent) for event in events)
            if args.offline:
                assert resets == 1
                searches = [
                    event.observation
                    for event in events
                    if isinstance(event, ObservationEvent)
                    and event.tool_name == "conversation_history"
                ]
                assert searches and "ZX-4916" in searches[-1].text
            cost = (
                conversation.conversation_stats.get_combined_metrics().accumulated_cost
            )
            print(f"Context resets: {resets}")
            print(f"EXAMPLE_COST: {cost}")
            conversation_id = conversation.id
        finally:
            conversation.close()

        with closing(
            LocalConversation(
                agent=agent,
                workspace=root,
                persistence_dir=root / "conversations",
                conversation_id=conversation_id,
                storage_safety=StorageSafetyConfig(min_free_ratio=0.05),
            )
        ) as restored:
            notes = restored.execute_tool(
                "context_notes", ContextNotesAction(command="read")
            )
            print("Notes restored from persisted events:", notes.text)
            if args.offline:
                assert "ZX-4916" in notes.text


if __name__ == "__main__":
    main()
