"""A keyed gateway request keeps its claim across server instances."""

from uuid import uuid4

import pytest

from openhands.agent_server.config import Config
from openhands.agent_server.openai import idempotency


@pytest.mark.asyncio
async def test_gateway_claim_survives_store_recreation(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    config = Config(conversations_path=tmp_path / "conversations")
    first_id = uuid4()
    first = await idempotency.GatewayIdempotencyStore(config).claim(
        key="caller-key-123",
        request_digest="body-a",
        conversation_id=first_id,
    )
    assert first.state == "new"

    restarted_store = idempotency.GatewayIdempotencyStore(config)
    pending = await restarted_store.claim(
        key="caller-key-123",
        request_digest="body-a",
        conversation_id=uuid4(),
    )
    assert pending.state == "pending"
    assert pending.conversation_id == first_id

    changed_request = await restarted_store.claim(
        key="caller-key-123",
        request_digest="body-b",
        conversation_id=uuid4(),
    )
    assert changed_request.state == "mismatch"

    assert first.claim_id is not None
    await restarted_store.complete(
        claim_id=first.claim_id, response={"id": "result-123"}
    )
    replay = await idempotency.GatewayIdempotencyStore(config).claim(
        key="caller-key-123",
        request_digest="body-a",
        conversation_id=uuid4(),
    )
    assert replay.state == "complete"
    assert replay.conversation_id == first_id
    assert replay.response == {"id": "result-123"}

    database = tmp_path / ".openhands/gateway-idempotency/claims.sqlite3"
    assert b"caller-key-123" not in database.read_bytes()
    assert database.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_gateway_does_not_evict_active_claim_to_accept_new_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    monkeypatch.setattr(idempotency, "_MAX_CLAIMS", 1)
    store = idempotency.GatewayIdempotencyStore(
        Config(conversations_path=tmp_path / "conversations")
    )
    first = await store.claim(
        key="first", request_digest="first", conversation_id=uuid4()
    )
    second = await store.claim(
        key="second", request_digest="second", conversation_id=uuid4()
    )
    retry = await store.claim(
        key="first", request_digest="first", conversation_id=uuid4()
    )
    assert first.state == "new"
    assert second.state == "full"
    assert retry.state == "pending"
