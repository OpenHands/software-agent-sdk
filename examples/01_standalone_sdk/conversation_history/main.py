"""Search condensed conversation history, page through it, and reopen it.

Run with:
    uv run python examples/01_standalone_sdk/conversation_history/main.py

This deterministic example uses TestLLM and makes no provider requests.
The normal LLMSummarizingCondenser remains responsible for condensation.
"""

import json
import tempfile
from contextlib import closing
from pathlib import Path

from openhands.sdk import Agent, LocalConversation, Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins import ConversationHistoryAction


def main() -> None:
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id="find-original",
                        name="conversation_history",
                        arguments=json.dumps(
                            {"command": "search", "query": "Release manifest"}
                        ),
                        origin="completion",
                    )
                ],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Found the original release manifest.")],
            ),
        ]
    )
    summary_llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Earlier release work was completed.")],
            )
        ],
        usage_id="summary",
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name="conversation_history")],
        condenser=LLMSummarizingCondenser(llm=summary_llm, keep_first=1, max_size=100),
    )
    original = "Release manifest\n" + "background\n" * 500 + "Release code: ZX-4916"
    with tempfile.TemporaryDirectory(prefix="history-example-") as directory:
        workspace = Path(directory)
        persistence_dir = workspace / "state"
        with closing(
            LocalConversation(
                agent=agent,
                workspace=workspace,
                persistence_dir=persistence_dir,
                visualizer=None,
            )
        ) as conversation:
            conversation.send_message(original)
            for i in range(8):
                conversation.send_message(f"Continue unrelated work item {i}.")
            conversation.condense()
            conversation.run()
            search = next(
                event.observation
                for event in conversation.state.active_branch()
                if isinstance(event, ObservationEvent)
                and event.tool_name == "conversation_history"
            )
            match = json.loads(search.text)["matches"][0]
            assert not match["in_active_view"]
            assert "ZX-4916" not in match["snippet"]
            event_id = match["event_id"]
            print(f"Found hidden source event: {event_id}")

            pages: list[str] = []
            offset = 0
            while True:
                result = conversation.execute_tool(
                    "conversation_history",
                    ConversationHistoryAction(
                        command="read", event_id=event_id, offset=offset
                    ),
                )
                assert not result.is_error, result.text
                page = json.loads(result.text)
                pages.append(page["text"])
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
            assert "".join(pages) == original
            print(f"Read {len(pages)} pages and recovered Release code: ZX-4916")
            conversation_id = conversation.id
            cost = (
                conversation.conversation_stats.get_combined_metrics().accumulated_cost
            )

        with closing(
            LocalConversation(
                agent=None,
                workspace=workspace,
                persistence_dir=persistence_dir,
                conversation_id=conversation_id,
                visualizer=None,
            )
        ) as reopened:
            result = reopened.execute_tool(
                "conversation_history",
                ConversationHistoryAction(
                    command="read", event_id=event_id, offset=4000
                ),
            )
            assert not result.is_error, result.text
            assert "ZX-4916" in json.loads(result.text)["text"]
            print("Reopened the conversation and read the same original event.")
    print(f"EXAMPLE_COST: {cost}")


if __name__ == "__main__":
    main()
