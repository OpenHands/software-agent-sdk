import asyncio

import pytest

from openhands.sdk import Agent, Conversation
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.message import MessageToolCall
from openhands.sdk.secret import LookupSecret
from openhands.sdk.testing import TestLLM


@pytest.mark.asyncio
async def test_async_response_masks_loopback_lookup_secret_without_blocking(tmp_path):
    requested = asyncio.Event()

    async def serve_secret(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        requested.set()
        body = b"loopback-secret-value"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve_secret, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="value loopback-secret-value")],
            )
        ]
    )
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        visualizer=None,
        secrets={"TEST_TOKEN": LookupSecret(url=f"http://127.0.0.1:{port}/secret")},
    )
    try:
        conversation.send_message("hello")
        await asyncio.wait_for(conversation.arun(), timeout=5)
        assert requested.is_set()
        messages = [
            event
            for event in conversation.state.events
            if isinstance(event, MessageEvent) and event.source == "agent"
        ]
        assert messages[-1].llm_message.content == [
            TextContent(text="value <secret-hidden>")
        ]
    finally:
        await asyncio.to_thread(conversation.close)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_async_tool_thought_matches_sync_behavior(tmp_path):
    thoughts = []
    for use_async in [False, True]:
        message = Message(
            role="assistant",
            content=[TextContent(text="existing tool thought")],
            tool_calls=[
                MessageToolCall(
                    id="finish-call",
                    origin="completion",
                    name="finish",
                    arguments='{"message":"done"}',
                )
            ],
        )
        conversation = Conversation(
            agent=Agent(llm=TestLLM.from_messages([message]), tools=[]),
            workspace=str(tmp_path),
            visualizer=None,
            secrets={"TOKEN": "existing tool thought"},
        )
        try:
            conversation.send_message("finish")
            if use_async:
                await conversation.arun()
            else:
                await asyncio.to_thread(conversation.run)
            thoughts.append(
                [
                    event.thought
                    for event in conversation.state.events
                    if isinstance(event, ActionEvent)
                ]
            )
        finally:
            await asyncio.to_thread(conversation.close)
    assert thoughts[0]
    assert thoughts[0] == thoughts[1]


@pytest.mark.asyncio
async def test_async_response_masks_secrets_registered_during_lookup(tmp_path):
    requested = []
    values = ["first-secret-value", "second-secret-value", "third-secret-value"]

    async def serve_secret(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        index = int(request.split(b" ")[1].removeprefix(b"/"))
        requested.append(index)
        if index + 1 < len(values):
            await asyncio.to_thread(
                conversation.update_secrets,
                {
                    f"TOKEN_{index + 1}": LookupSecret(
                        url=f"http://127.0.0.1:{port}/{index + 1}"
                    )
                },
            )
        body = values[index].encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve_secret, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    conversation = Conversation(
        agent=Agent(
            llm=TestLLM.from_messages(
                [
                    Message(
                        role="assistant", content=[TextContent(text=" ".join(values))]
                    )
                ]
            ),
            tools=[],
        ),
        workspace=str(tmp_path),
        visualizer=None,
        secrets={"TOKEN_0": LookupSecret(url=f"http://127.0.0.1:{port}/0")},
    )
    try:
        conversation.send_message("hello")
        await asyncio.wait_for(conversation.arun(), timeout=5)
        assert requested == [0, 1, 2]
        messages = [
            event
            for event in conversation.state.events
            if isinstance(event, MessageEvent) and event.source == "agent"
        ]
        assert messages[-1].llm_message.content == [
            TextContent(text="<secret-hidden> <secret-hidden> <secret-hidden>")
        ]
    finally:
        await asyncio.to_thread(conversation.close)
        server.close()
        await server.wait_closed()
