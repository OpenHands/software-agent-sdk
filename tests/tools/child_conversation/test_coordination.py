import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from openhands.sdk import Agent, Conversation, Message, TextContent, Tool
from openhands.sdk.event.mailbox import MailboxMessage
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.testing import TestLLM
from openhands.tools.child_conversation import WaitForChildrenTool
from openhands.tools.child_conversation.wait import (
    SendMailboxMessageAction,
    SendMailboxMessageExecutor,
    WaitForChildrenAction,
    WaitForChildrenExecutor,
)


@pytest.mark.parametrize("mailbox", [False, True])
def test_canonical_tool_payload_and_auth(monkeypatch, mailbox):
    sender, recipient = uuid4(), uuid4()
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://coordination.test")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "coordination-session-key")

    async def handle(request):
        assert request.headers["X-Session-API-Key"] == "coordination-session-key"
        body = json.loads(request.content)
        assert request.url.path == (
            f"/api/conversations/{sender}/mailbox"
            if mailbox
            else f"/api/conversations/{sender}/children/wait"
        )
        if mailbox:
            assert body["kind"] == "question" and body["idempotency_key"] == "stable"
            return httpx.Response(200, json={"message_id": "one", "duplicate": False})
        assert body["conversation_ids"] == [str(recipient)]
        assert "kind" not in body
        return httpx.Response(200, json={"children": [], "timed_out": True})

    executor = (
        SendMailboxMessageExecutor(sender, httpx.MockTransport(handle))
        if mailbox
        else WaitForChildrenExecutor(sender, httpx.MockTransport(handle))
    )
    action = (
        SendMailboxMessageAction.model_validate(
            {
                "message": {
                    "recipient_id": recipient,
                    "kind": "question",
                    "body": "Hello",
                    "idempotency_key": "stable",
                }
            }
        )
        if mailbox
        else WaitForChildrenAction(conversation_ids=[recipient])
    )
    response = executor(action)
    assert not response.is_error
    assert not executor._tasks


def test_sync_agent_releases_parent_lock_while_wait_tool_blocks(tmp_path):
    child = uuid4()
    entered, release = threading.Event(), threading.Event()

    async def handle(request):
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return httpx.Response(200, json={"children": [], "timed_out": True})

    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id="wait",
                        name=WaitForChildrenTool.name,
                        arguments=json.dumps({"conversation_ids": [str(child)]}),
                        origin="completion",
                    )
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Finished")]),
        ]
    )
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[Tool(name=WaitForChildrenTool.name)]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    try:
        conversation.send_message("Wait for child")
        executor = conversation.agent.tools_map[WaitForChildrenTool.name].executor
        assert isinstance(executor, WaitForChildrenExecutor)
        executor.transport = httpx.MockTransport(handle)
        with ThreadPoolExecutor(max_workers=2) as pool:
            run = pool.submit(conversation.run)
            try:
                assert entered.wait(5)
                delivery = pool.submit(
                    conversation.receive_mailbox_message,
                    MailboxMessage(
                        recipient_id=conversation.id,
                        sender_id=child,
                        message_id="sync-mail",
                        idempotency_key="sync",
                        kind="decision",
                        body="Update during wait",
                    ),
                )
                assert delivery.result(timeout=2).message_id == "sync-mail"
            finally:
                release.set()
            run.result(timeout=5)
        assert not executor._tasks
    finally:
        conversation.close()
