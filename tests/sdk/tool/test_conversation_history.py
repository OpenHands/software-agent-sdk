import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing

import pytest

from openhands.sdk import Agent, LocalConversation, Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins import ConversationHistoryAction


def history_call(call_id: str, **arguments: object) -> MessageToolCall:
    return MessageToolCall(
        id=call_id,
        name="conversation_history",
        arguments=json.dumps(arguments),
        origin="completion",
    )


def read_history(conversation: LocalConversation, **arguments: object) -> dict:
    observation = conversation.execute_tool(
        "conversation_history", ConversationHistoryAction.model_validate(arguments)
    )
    assert not observation.is_error, observation.text
    return json.loads(observation.text)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("concurrency", [1, 2])
@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_history_retrieves_original_after_summary(
    history_conversation_factory, asynchronous: bool, concurrency: int
):
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                tool_calls=[
                    history_call("one", command="search", query="a.b"),
                    history_call("two", command="search", query="A.B"),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done.")]),
        ]
    )
    summarizer = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Work continued.")])],
        usage_id="summary",
    )
    conversation = history_conversation_factory(
        Agent(
            llm=llm,
            tools=[Tool(name="conversation_history")],
            condenser=LLMSummarizingCondenser(
                llm=summarizer, keep_first=1, max_size=100
            ),
            tool_concurrency_limit=concurrency,
        )
    )
    original_text = "Original A.B " + "x" * 8200 + " exact-value"
    conversation.send_message(original_text)
    original = conversation.state.active_branch()[-1]
    for i in range(8):
        conversation.send_message(f"Continue unrelated task {i}.")
    conversation.condense()
    assert original.id not in {event.id for event in conversation.state.view.events}

    if asynchronous:
        await conversation.arun()
    else:
        conversation.run()

    results = [
        json.loads(event.observation.text)
        for event in conversation.state.active_branch()
        if isinstance(event, ObservationEvent)
        and event.tool_name == "conversation_history"
    ]
    assert len(results) == 2
    for result in results:
        match = result["matches"][0]
        assert match["event_id"] == original.id
        assert not match["in_active_view"]
        assert len(match["snippet"]) == 400
        assert "exact-value" not in match["snippet"]

    pages = []
    offset = 0
    while True:
        page = read_history(
            conversation, command="read", event_id=original.id, offset=offset
        )
        assert not page["in_active_view"]
        assert len(page["text"]) <= 4000
        pages.append(page["text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(pages) == original_text
    assert len(pages) == 3
    assert summarizer.call_count == 1


def test_history_pagination_branch_and_reopen(history_conversation_factory, tmp_path):
    conversation = history_conversation_factory()
    events = []
    for i in range(8):
        conversation.send_message(f"Match A.B {i}")
        events.append(conversation.state.active_branch()[-1])
    conversation.send_message("Axb is not a literal match.")
    first = read_history(conversation, command="search", query="a.b")
    assert [m["event_id"] for m in first["matches"]] == [
        event.id for event in reversed(events[3:])
    ]
    second = read_history(
        conversation,
        command="search",
        query="a.b",
        before_event_id=first["next_before_event_id"],
    )
    assert [m["event_id"] for m in second["matches"]] == [
        event.id for event in reversed(events[:3])
    ]
    assert second["next_before_event_id"] is None

    with closing(conversation.fork(from_event_id=events[0].id)) as fork:
        fork_result = read_history(fork, command="search", query="a.b")
        assert [m["event_id"] for m in fork_result["matches"]] == [events[0].id]
        inaccessible = fork.execute_tool(
            "conversation_history",
            ConversationHistoryAction(command="read", event_id=events[-1].id),
        )
        assert inaccessible.is_error

    conversation.navigate_to(events[0].id)
    conversation_id = conversation.id
    conversation.close()
    with closing(
        LocalConversation(
            agent=None,
            workspace=tmp_path,
            persistence_dir=tmp_path / "state",
            conversation_id=conversation_id,
            visualizer=None,
        )
    ) as reopened:
        assert (
            read_history(reopened, command="read", event_id=events[0].id)["text"]
            == "Match A.B 0"
        )
        invalid_cursor = reopened.execute_tool(
            "conversation_history",
            ConversationHistoryAction(
                command="search", query="a.b", before_event_id=events[-1].id
            ),
        )
        assert invalid_cursor.is_error


def test_history_long_query_and_empty_end_pages(history_conversation_factory):
    conversation = history_conversation_factory()
    text = "long literal " * 30
    conversation.send_message(text)
    original = conversation.state.active_branch()[-1]
    result = read_history(conversation, command="search", query=text)
    assert result["matches"][0]["event_id"] == original.id
    for offset in [len(text), len(text) + 100]:
        page = read_history(
            conversation, command="read", event_id=original.id, offset=offset
        )
        assert page["text"] == ""
        assert page["next_offset"] is None


def test_history_excludes_internal_reasoning_and_its_own_results(
    history_conversation_factory,
):
    conversation = history_conversation_factory(
        Agent(
            llm=TestLLM.from_messages(
                [
                    Message(
                        role="assistant",
                        content=[TextContent(text="Visible answer")],
                        reasoning_content="private-reasoning-only",
                    ),
                    Message(
                        role="assistant",
                        tool_calls=[
                            history_call(
                                "search", command="search", query="Visible answer"
                            )
                        ],
                    ),
                    Message(role="assistant", content=[TextContent(text="Done.")]),
                ]
            ),
            tools=[Tool(name="ConversationHistoryTool")],
        )
    )
    conversation.send_message("Reply, then we will search.")
    conversation.run()
    conversation.send_message("Search the previous answer.")
    conversation.run()
    for query in ["private-reasoning-only", '"matches"']:
        assert (
            read_history(conversation, command="search", query=query)["matches"] == []
        )
    internal = conversation.execute_tool(
        "conversation_history",
        ConversationHistoryAction(
            command="read", event_id=conversation.state.active_branch()[0].id
        ),
    )
    assert internal.is_error
    answer = next(
        event
        for event in conversation.state.active_branch()
        if isinstance(event, MessageEvent) and event.source == "agent"
    )
    assert (
        read_history(conversation, command="read", event_id=answer.id)["text"]
        == "Visible answer"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"command": "search", "query": " "},
        {"command": "search", "query": "x", "event_id": "event"},
        {"command": "search", "query": "x", "offset": 1},
        {"command": "read"},
        {"command": "read", "event_id": "event", "query": "x"},
        {"command": "read", "event_id": "event", "before_event_id": "cursor"},
        {"command": "read", "event_id": "unavailable"},
    ],
)
def test_history_rejects_invalid_requests(history_conversation_factory, arguments):
    conversation = history_conversation_factory()
    observation = conversation.execute_tool(
        "conversation_history", ConversationHistoryAction.model_validate(arguments)
    )
    assert observation.is_error


def test_direct_history_waits_for_a_consistent_branch_snapshot(
    history_conversation_factory,
):
    conversation = history_conversation_factory()
    conversation.send_message("Match root")
    root = conversation.state.active_branch()[-1]
    conversation.send_message("Match abandoned")
    started = threading.Event()

    def search():
        started.set()
        return read_history(conversation, command="search", query="Match")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with conversation.state:
            pending = pool.submit(search)
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.05)
            conversation.navigate_to(root.id)
        result = pending.result(timeout=5)
    assert [match["event_id"] for match in result["matches"]] == [root.id]
    assert result["matches"][0]["in_active_view"]
