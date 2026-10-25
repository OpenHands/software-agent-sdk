"""Mailbox persistence, delivery and goal-loop behavior through real services."""

import asyncio
import json
from threading import Event
from typing import cast
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import PrivateAttr, ValidationError

from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import (
    ChildConversationNotFound,
    ConversationService,
)
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import (
    StartConversationRequest,
    UpdateConversationRequest,
)
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.child_wait import WaitForChildrenRequest
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.event.mailbox import MailboxConflict, MailboxSendRequest
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import LocalWorkspace


async def _create(service, workspace, parent=None):
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="mailbox-test")),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            parent_conversation_id=parent,
        )
    )
    await service.update_conversation(
        info.id, UpdateConversationRequest(title="Mailbox member")
    )
    return await service.get_event_service(info.id)


@pytest.fixture
async def mailbox_family(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent = await _create(service, workspace)
        first = await _create(service, workspace, parent.stored.id)
        second = await _create(service, workspace, parent.stored.id)
        yield service, parent, first, second


def _mail(service):
    return [
        event
        for event in service.get_conversation().state.events
        if isinstance(event, MessageEvent) and event.mailbox is not None
    ]


@pytest.mark.parametrize("kind", ["proposal", "review-request", "blocker"])
def test_required_evidence_and_invalid_delivery(kind):
    with pytest.raises(ValidationError):
        MailboxSendRequest(
            recipient_id=uuid4(), idempotency_key="one", kind=kind, body="body"
        )
    with pytest.raises(ValidationError):
        MailboxSendRequest.model_validate(
            {
                "recipient_id": uuid4(),
                "idempotency_key": "one",
                "kind": "decision",
                "body": "body",
                "delivery": "queue",
            }
        )


async def test_typed_delivery_attribution_replay_and_conflict(mailbox_family):
    service, _, sender, recipient = mailbox_family
    request = MailboxSendRequest(
        recipient_id=recipient.stored.id,
        idempotency_key="review-1",
        kind="review-request",
        body="Please review",
        evidence=["commit abc"],
    )
    first = await service.send_mailbox_message(sender.stored.id, request)
    again = await service.send_mailbox_message(sender.stored.id, request)
    assert first.message_id == again.message_id
    assert not first.duplicate and again.duplicate
    assert len(_mail(recipient)) == 1

    event = _mail(recipient)[0]
    assert event.mailbox is not None
    assert event.source == "environment" and event.sender == str(sender.stored.id)
    assert event.mailbox.kind == "review-request"
    assert event.mailbox.evidence == ["commit abc"]
    with pytest.raises(MailboxConflict):
        await service.send_mailbox_message(
            sender.stored.id, request.model_copy(update={"body": "different"})
        )
    assert len(_mail(recipient)) == 1


async def test_concurrent_retries_append_only_once(mailbox_family):
    service, sender, recipient, _ = mailbox_family
    request = MailboxSendRequest(
        recipient_id=recipient.stored.id,
        idempotency_key="concurrent",
        kind="decision",
        body="One intended message",
    )
    responses = await asyncio.gather(
        *(service.send_mailbox_message(sender.stored.id, request) for _ in range(8))
    )
    assert len({response.message_id for response in responses}) == 1
    assert sum(not response.duplicate for response in responses) == 1
    assert len(_mail(recipient)) == 1


async def test_sender_and_recipient_secrets_are_redacted(mailbox_family):
    service, sender, recipient, _ = mailbox_family
    sender.get_conversation().update_secrets({"SENDER_KEY": "sender-secret-value"})
    recipient.get_conversation().update_secrets(
        {"RECIPIENT_KEY": "recipient-secret-value"}
    )
    request = MailboxSendRequest(
        recipient_id=recipient.stored.id,
        idempotency_key="redacted",
        kind="proposal",
        body="sender-secret-value recipient-secret-value",
        evidence=["sender-secret-value recipient-secret-value"],
    )
    first = await service.send_mailbox_message(sender.stored.id, request)
    again = await service.send_mailbox_message(sender.stored.id, request)
    assert again.message_id == first.message_id and again.duplicate
    serialized = _mail(recipient)[0].model_dump_json()
    assert "sender-secret-value" not in serialized
    assert "recipient-secret-value" not in serialized


async def test_tree_boundary_and_http_validation(mailbox_family):
    service, parent, sender, recipient = mailbox_family
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: service
    body = dict(
        recipient_id=str(recipient.stored.id),
        idempotency_key="one",
        kind="decision",
        body="hello",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        url = f"/api/conversations/{sender.stored.id}/mailbox"
        assert (await client.post(url, json=body)).status_code == 200
        assert (
            await client.post(url, json=body | {"body": "different"})
        ).status_code == 409
        assert (
            await client.post(url, json=body | {"kind": "blocker"})
        ).status_code == 422
        assert (
            await client.post(url, json=body | {"recipient_id": str(uuid4())})
        ).status_code == 404
    with pytest.raises(ChildConversationNotFound):
        await service.send_mailbox_message(
            parent.stored.id,
            MailboxSendRequest(
                recipient_id=parent.stored.id,
                idempotency_key="self",
                kind="decision",
                body="hello",
            ),
        )

    outsider = await _create(service, parent.stored.workspace.working_dir)
    with pytest.raises(ChildConversationNotFound):
        await service.send_mailbox_message(
            sender.stored.id,
            MailboxSendRequest(
                recipient_id=outsider.stored.id,
                idempotency_key="outside",
                kind="decision",
                body="Must not be delivered",
            ),
        )
    assert not _mail(outsider)


async def test_offline_message_and_idempotency_survive_service_restart(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = tmp_path / "conversations"
    async with ConversationService(conversations_dir=directory) as service:
        parent = await _create(service, workspace)
        child = await _create(service, workspace, parent.stored.id)
        parent_id, child_id = parent.stored.id, child.stored.id
        request = MailboxSendRequest(
            recipient_id=child_id,
            idempotency_key="durable",
            kind="question",
            body="Remember this",
        )
        first = await service.send_mailbox_message(parent_id, request)
    async with ConversationService(conversations_dir=directory) as restored:
        again = await restored.send_mailbox_message(parent_id, request)
        assert again.message_id == first.message_id and again.duplicate
        child = await restored.get_event_service(child_id)
        assert child is not None
        assert len(_mail(child)) == 1
        mail = _mail(child)[0].mailbox
        assert mail is not None and mail.body == "Remember this"
        llm = cast(
            _GatedLLM,
            _GatedLLM.from_messages(
                [Message(role="assistant", content=[TextContent(text="Read it")])],
                usage_id="restored-mailbox",
            ),
        )
        llm._release.set()
        child.get_conversation().switch_llm(llm)
        await child.run()
        task = child._run_task
        assert task is not None
        await asyncio.wait_for(task, 10)
        assert llm._requests[0][0].role == "system"
        assert any("Remember this" in str(m.content) for m in llm._requests[0])
        waited = await restored.wait_for_children(
            parent_id, WaitForChildrenRequest(conversation_ids=[child_id])
        )
        result_message_id = waited.children[0].mailbox_message_id
        assert result_message_id
    async with ConversationService(conversations_dir=directory) as restarted:
        waited = await restarted.wait_for_children(
            parent_id, WaitForChildrenRequest(conversation_ids=[child_id])
        )
        assert waited.children[0].mailbox_message_id == result_message_id
        parent = await restarted.get_event_service(parent_id)
        assert parent is not None and len(_mail(parent)) == 1


async def test_wait_delivers_actual_result_before_child_deletion(mailbox_family):
    service, parent, child, _ = mailbox_family
    child.get_conversation().switch_llm(
        TestLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text="Completed the delegated task")],
                )
            ],
            usage_id="child-result",
        )
    )
    await child.send_message(
        Message(role="user", content=[TextContent(text="Work")]), run=True
    )
    response = await service.wait_for_children(
        parent.stored.id,
        WaitForChildrenRequest(
            conversation_ids=[child.stored.id],
            timeout_seconds=10,
            delete_on_finish=False,
        ),
    )
    result = response.children[0]
    assert result.execution_status.value == "finished", [
        event.model_dump(mode="json") for event in child.get_conversation().state.events
    ]
    assert result.settled and result.mailbox_message_id
    message = _mail(parent)[0]
    assert message.mailbox is not None
    assert message.id == result.mailbox_message_id
    assert json.loads(message.mailbox.body)["result"] == "Completed the delegated task"
    repeated = await service.wait_for_children(
        parent.stored.id,
        WaitForChildrenRequest(
            conversation_ids=[child.stored.id], delete_on_finish=True
        ),
    )
    assert repeated.children[0].mailbox_message_id == result.mailbox_message_id
    assert repeated.children[0].deleted and len(_mail(parent)) == 1
    assert await service.get_conversation(child.stored.id) is None


class _GatedLLM(TestLLM):
    _entered: Event = PrivateAttr(default_factory=Event)
    _release: Event = PrivateAttr(default_factory=Event)
    _requests: list = PrivateAttr(default_factory=list)

    def completion(self, messages, *args, **kwargs):
        self._requests.append(messages)
        if self._call_count == 0:
            self._entered.set()
            assert self._release.wait(10)
        return super().completion(messages, *args, **kwargs)


@pytest.mark.parametrize("delivery", ["steer", "interrupt"])
async def test_mailbox_preserves_or_explicitly_interrupts_real_goal_loop(
    mailbox_family, delivery
):
    service, parent, child, _ = mailbox_family
    llm = cast(
        _GatedLLM,
        _GatedLLM.from_messages(
            [
                Message(role="assistant", content=[TextContent(text="Initial work")]),
                Message(
                    role="assistant", content=[TextContent(text="Mailbox incorporated")]
                ),
            ],
            usage_id="gated-goal",
        ),
    )
    child.get_conversation().switch_llm(llm)
    judge = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[
                    TextContent(text='{"score": 1.0, "complete": true, "missing": ""}')
                ],
            )
        ],
        usage_id="judge",
    )
    await child.start_goal_loop("Complete the work", judge_llm=judge)
    goal_task = child._goal_loop_task
    assert goal_task is not None
    try:
        assert await asyncio.to_thread(llm._entered.wait, 5)
        request = MailboxSendRequest(
            recipient_id=child.stored.id,
            idempotency_key="goal-message",
            kind="decision",
            body="Important teammate update",
            delivery=delivery,
        )
        delivered = await service.send_mailbox_message(parent.stored.id, request)
        duplicate = await service.send_mailbox_message(parent.stored.id, request)
        assert duplicate.duplicate and duplicate.message_id == delivered.message_id
        if delivery == "steer":
            assert not goal_task.done()
        llm._release.set()
        outcome = await asyncio.wait_for(
            asyncio.gather(goal_task, return_exceptions=True), 10
        )
        if delivery == "steer":
            assert outcome == [None]
        else:
            assert isinstance(outcome[0], asyncio.CancelledError)
        updates = [
            e.value
            for e in child.get_conversation().state.events
            if isinstance(e, ConversationStateUpdateEvent) and e.key == "goal"
        ]
        assert updates[-1]["status"] == (
            "complete" if delivery == "steer" else "interrupted"
        )
        if delivery == "steer":
            assert len(llm._requests) == 2
            assert any(
                "Important teammate update" in str(message.content)
                for message in llm._requests[-1]
            )
        mail = _mail(child)[0].mailbox
        assert mail is not None and mail.delivery == delivery
    finally:
        llm._release.set()
