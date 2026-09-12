import asyncio

import pytest

from openhands.sdk import Agent, Conversation
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
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
