import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from openhands.agent_server.config import WebhookSpec
from openhands.agent_server.durable_webhook import DurableWebhookSubscriber
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk.event import ConversationStateUpdateEvent
from openhands.sdk.workspace import LocalWorkspace


def _publisher(path: Path, local_path: Path):
    stored = StoredConversation(
        id=uuid4(), workspace=LocalWorkspace(working_dir=str(path))
    )
    service = EventService.for_persisted_events(stored, local_path)
    subscriber = DurableWebhookSubscriber(
        stored.id,
        service,
        WebhookSpec(
            base_url="https://example.com",
            durable_events=True,
            event_buffer_size=2,
            max_queue_size=1,
            max_queue_bytes=1,
        ),
        "test-key",
    )
    return service, subscriber


@pytest.mark.asyncio
async def test_durable_delivery_resumes_after_lost_ack_and_restart(tmp_path):
    local_path = tmp_path / "conversations"
    service, subscriber = _publisher(tmp_path, local_path)
    for _ in range(5):
        event = ConversationStateUpdateEvent(key="execution_status", value="idle")
        service._events_for_read().append(event)

    acked = 0
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal acked
        assert request.headers["X-Session-API-Key"] == "test-key"
        assert request.url.path == f"/event-stream/{subscriber.conversation_id.hex}"
        if request.method == "GET":
            return httpx.Response(200, json={"acked_through": acked})
        seqs = [entry["seq"] for entry in json.loads(request.content)["events"]]
        requests.append(seqs)
        acked = seqs[-1]
        if len(requests) == 1:
            raise httpx.ReadError("Lost the response", request=request)
        return httpx.Response(200, json={"acked_through": acked})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, headers={"X-Session-API-Key": "test-key"}
    ) as client:
        with pytest.raises(httpx.ReadError, match="Lost the response"):
            await subscriber._drain(client)
        assert subscriber._acked == 0
        restarted = DurableWebhookSubscriber(
            subscriber.conversation_id,
            EventService.for_persisted_events(service.stored, local_path),
            subscriber.spec,
        )
        await restarted._drain(client)
        assert restarted._acked == 4
    assert requests == [[1, 2], [3, 4]]


@pytest.mark.asyncio
async def test_durable_delivery_retries_same_batch_when_legacy_ack_has_no_cursor(
    tmp_path,
):
    service, subscriber = _publisher(tmp_path, tmp_path / "conversations")
    service._events_for_read().append(
        ConversationStateUpdateEvent(key="execution_status", value="idle")
    )
    attempts = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"acked_through": -1})
        attempts.append(json.loads(request.content))
        data = {"acked_through": 0} if len(attempts) > 1 else {"success": True}
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="Invalid durable webhook acknowledgement"):
            await subscriber._drain(client)
        assert subscriber._acked == -1
        await subscriber._drain(client)

    assert subscriber._acked == 0
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]


def test_durable_delivery_stops_at_missing_sequence(tmp_path):
    local_path = tmp_path / "conversations"
    service, subscriber = _publisher(tmp_path, local_path)
    for _ in range(3):
        service._events_for_read().append(
            ConversationStateUpdateEvent(key="execution_status", value="idle")
        )
    events_dir = local_path / subscriber.conversation_id.hex / "events"
    next(events_dir.glob("event-00001-*.json")).unlink()
    reopened = EventService.for_persisted_events(service.stored, local_path)

    assert [entry["seq"] for entry in reopened.webhook_event_batch(-1, 10, 10000)] == [
        0
    ]
    with pytest.raises(ValueError, match="Missing persisted event at sequence 1"):
        reopened.webhook_event_batch(0, 10, 10000)


def test_replay_snapshot_does_not_include_newly_appended_events(tmp_path):
    service, _ = _publisher(tmp_path, tmp_path / "conversations")
    log = service._events_for_read()
    for _ in range(2):
        log.append(ConversationStateUpdateEvent(key="execution_status", value="idle"))

    first = service.webhook_replay_page(-1, None, 1)
    assert first["through_seq"] == 1
    assert [entry["seq"] for entry in first["events"]] == [0]

    log.append(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    second = service.webhook_replay_page(0, first["through_seq"], 100)
    assert [entry["seq"] for entry in second["events"]] == [1]
    assert service.webhook_replay_page(1, first["through_seq"], 100)["events"] == []
    with pytest.raises(ValueError, match="outside the persisted log"):
        service.webhook_replay_page(1, 3, 100)


@pytest.mark.asyncio
async def test_durable_close_drains_events_written_during_teardown(tmp_path):
    service, subscriber = _publisher(tmp_path, tmp_path / "conversations")
    final_event = ConversationStateUpdateEvent(key="execution_status", value="paused")
    conversation = MagicMock()
    conversation.close.side_effect = lambda: service._events_for_read().append(
        final_event
    )
    service._conversation = conversation
    service._pub_sub.subscribe(subscriber)
    received_ids = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"acked_through": -1})
        received_ids.extend(
            entry["event"]["id"] for entry in json.loads(request.content)["events"]
        )
        return httpx.Response(200, json={"acked_through": 0})

    with patch.object(
        subscriber,
        "_client",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ):
        await service.close()

    assert received_ids == [final_event.id]
