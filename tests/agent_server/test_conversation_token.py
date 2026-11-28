from types import SimpleNamespace
from uuid import uuid4

import pytest

from openhands.agent_server import (
    conversation_token,
    mint_conversation_token,
    verify_conversation_token,
)
from openhands.agent_server.conversation_token import InvalidConversationTokenError


def test_round_trip_returns_conversation_id():
    conversation_id = uuid4()
    token = mint_conversation_token("key-a", conversation_id, ttl=60)

    assert token.startswith("oh1.")
    assert verify_conversation_token(token, ["key-a"]) == conversation_id


def test_any_configured_key_verifies():
    conversation_id = uuid4()
    token = mint_conversation_token("key-b", str(conversation_id), ttl=60)

    assert verify_conversation_token(token, ["key-a", "key-b"]) == conversation_id


def test_key_no_longer_configured_is_rejected():
    token = mint_conversation_token("retired-key", uuid4(), ttl=60)

    with pytest.raises(InvalidConversationTokenError):
        verify_conversation_token(token, ["key-a"])


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(conversation_token, "time", SimpleNamespace(time=lambda: 1000))
    token = mint_conversation_token("key-a", uuid4(), ttl=60)
    monkeypatch.setattr(conversation_token, "time", SimpleNamespace(time=lambda: 1060))

    with pytest.raises(InvalidConversationTokenError):
        verify_conversation_token(token, ["key-a"])


def test_tampered_payload_is_rejected():
    token = mint_conversation_token("key-a", uuid4(), ttl=60)
    prefix, payload, signature = token.split(".")
    forged = mint_conversation_token("key-a", uuid4(), ttl=60).split(".")[1]

    with pytest.raises(InvalidConversationTokenError):
        verify_conversation_token(f"{prefix}.{forged}.{signature}", ["key-a"])
    assert payload != forged


@pytest.mark.parametrize(
    "token",
    [
        "",
        "sk-session-key",
        "oh1.",
        "oh1.payload",
        "oh1.a.b.c",
        "oh1.e30.",
        "oh1.ïnvalid.sïgnature",
    ],
)
def test_malformed_token_is_rejected(token):
    with pytest.raises(InvalidConversationTokenError):
        verify_conversation_token(token, ["key-a"])


@pytest.mark.parametrize("ttl", [0, -1])
def test_mint_rejects_non_positive_ttl(ttl):
    with pytest.raises(ValueError):
        mint_conversation_token("key-a", uuid4(), ttl=ttl)


def test_mint_rejects_invalid_conversation_id():
    with pytest.raises(ValueError):
        mint_conversation_token("key-a", "not-a-uuid", ttl=60)
