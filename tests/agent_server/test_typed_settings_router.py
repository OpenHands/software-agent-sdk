"""Tests for the typed settings endpoints + ETag / If-Match support.

These exercise:

* GET ``/api/settings`` now returns an ``ETag`` header.
* The legacy ``PATCH /api/settings`` honours ``If-Match`` (412 on mismatch)
  and emits a ``Deprecation`` header.
* New ``PUT/PATCH /api/settings/agent`` (typed, ``extra="forbid"``).
* New ``PUT/PATCH /api/settings/conversation`` (typed, ``extra="forbid"``).
* Variant invariance on PATCH agent (no implicit ``openhands`` ↔ ``acp`` switch).
* Optimistic concurrency: 412 when the persisted state has moved since the
  client captured an ETag.

Shares fixtures with ``test_settings_router.py``.
"""

from __future__ import annotations

import os
import tempfile
from base64 import urlsafe_b64encode
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores


@pytest.fixture
def temp_persistence_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        reset_stores()
        old_val = os.environ.get("OH_PERSISTENCE_DIR")
        os.environ["OH_PERSISTENCE_DIR"] = tmpdir
        yield Path(tmpdir)
        reset_stores()
        if old_val is not None:
            os.environ["OH_PERSISTENCE_DIR"] = old_val
        else:
            os.environ.pop("OH_PERSISTENCE_DIR", None)


@pytest.fixture
def secret_key():
    return urlsafe_b64encode(b"a" * 32).decode("ascii")


@pytest.fixture
def client(temp_persistence_dir, secret_key):
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(secret_key),
    )
    test_client = TestClient(create_app(config))
    # Seed a deterministic settings file. ``PersistedSettings()`` defaults
    # include ``AgentContext.current_datetime = datetime.now()`` which makes
    # two fresh instances genuinely-different state (and therefore different
    # ETags). For these tests we want a stable starting point so a captured
    # ETag survives until something actually changes.
    test_client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4"}}},
    )
    return test_client


# ── ETag on GET ────────────────────────────────────────────────────────────


def test_get_settings_returns_etag_header(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.headers.get("ETag", "").startswith('"')


def test_etag_stable_across_identical_saves(client):
    """Two no-op PATCHes (writing the same agent.llm.model) yield the same
    ETag, even though the on-disk Fernet ciphertext changes per save.

    This is the core idempotency property the legacy byte-hash ETag
    couldn't provide.
    """
    client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4o"}},
    )
    etag1 = client.get("/api/settings").headers["ETag"]

    client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4o"}},
    )
    etag2 = client.get("/api/settings").headers["ETag"]
    assert etag1 == etag2


def test_etag_changes_on_real_change(client):
    client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4"}},
    )
    etag1 = client.get("/api/settings").headers["ETag"]

    client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4o"}},
    )
    etag2 = client.get("/api/settings").headers["ETag"]
    assert etag1 != etag2


# ── If-Match / optimistic concurrency ─────────────────────────────────────


def test_patch_agent_with_matching_if_match_succeeds(client):
    etag = client.get("/api/settings").headers["ETag"]
    response = client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4o"}},
        headers={"If-Match": etag},
    )
    assert response.status_code == 200
    assert response.headers["ETag"] != etag  # new state, new ETag


def test_patch_agent_with_stale_if_match_returns_412(client):
    etag = client.get("/api/settings").headers["ETag"]
    # Another client mutates first.
    client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4o"}},
    )

    # Stale etag → 412 + current ETag echoed back.
    response = client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4"}},
        headers={"If-Match": etag},
    )
    assert response.status_code == 412
    assert response.headers["ETag"] != etag


def test_put_agent_with_wildcard_if_match_succeeds(client):
    """``If-Match: *`` only requires the resource to exist; it always does
    here because settings default."""
    response = client.put(
        "/api/settings/agent",
        json={
            "agent_kind": "openhands",
            "llm": {"model": "gpt-4o", "usage_id": "u"},
        },
        headers={"If-Match": "*"},
    )
    assert response.status_code == 200


def test_legacy_patch_honours_if_match(client):
    """The legacy PATCH /api/settings now also honours If-Match, so existing
    clients can opt into safety without switching shapes."""
    etag = client.get("/api/settings").headers["ETag"]
    # Move state forward.
    client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )

    response = client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4"}}},
        headers={"If-Match": etag},
    )
    assert response.status_code == 412


def test_legacy_patch_sends_deprecation_header(client):
    response = client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )
    assert response.status_code == 200
    assert response.headers.get("Deprecation") == "true"
    assert "/api/settings/agent" in response.headers.get("Link", "")


# ── Typed PATCH /api/settings/agent ───────────────────────────────────────


def test_patch_agent_partial_preserves_api_key(client):
    """The big practical win: a partial ``llm`` update keeps ``api_key``
    intact, so clients don't have to round-trip secrets just to change a
    model name."""
    # Seed an API key via legacy PATCH (the only way to set secrets here).
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {"llm": {"model": "gpt-4", "api_key": "sk-original"}}
        },
    )

    # Typed partial: change only the model.
    response = client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 200

    # Read back with plaintext exposure: api_key must still be 'sk-original'.
    response = client.get("/api/settings", headers={"X-Expose-Secrets": "plaintext"})
    agent_settings = response.json()["agent_settings"]
    assert agent_settings["llm"]["model"] == "gpt-4o"
    assert agent_settings["llm"]["api_key"] == "sk-original"


def test_patch_agent_unknown_field_returns_422(client):
    """``extra="forbid"`` on the typed update model — typos fail loudly
    instead of being silently merged."""
    response = client.patch(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "lllm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 422


def test_patch_agent_variant_mismatch_returns_422(client):
    """PATCH /agent does NOT switch ``agent_kind``. To go openhands → acp,
    callers must use PUT."""
    response = client.patch(
        "/api/settings/agent",
        json={"agent_kind": "acp", "acp_model": "claude"},
    )
    assert response.status_code == 422
    assert "agent_kind" in response.json()["detail"].lower()


def test_patch_agent_legacy_llm_discriminator_accepted(client):
    """The deprecated ``agent_kind: 'llm'`` alias maps to the openhands
    variant for PATCH compatibility."""
    response = client.patch(
        "/api/settings/agent",
        json={"agent_kind": "llm", "llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 200


# ── PUT /api/settings/agent (full replace + variant switch) ───────────────


def test_put_agent_full_replace(client):
    response = client.put(
        "/api/settings/agent",
        json={
            "agent_kind": "openhands",
            "llm": {"model": "gpt-4o", "usage_id": "u"},
            "agent": "CodeActAgent",
        },
    )
    assert response.status_code == 200
    new_etag = response.headers["ETag"]
    assert new_etag.startswith('"')

    # PUT does *not* honour the legacy "extra ignored" behaviour — it
    # validates against the source model fully.
    response = client.get("/api/settings")
    assert response.json()["agent_settings"]["llm"]["model"] == "gpt-4o"


def test_put_agent_can_switch_variant_to_acp(client):
    """The new endpoint is the canonical way to switch agent variants —
    something the legacy deep-merge endpoint could only do incidentally
    (and with leftover junk fields)."""
    response = client.put(
        "/api/settings/agent",
        json={
            "agent_kind": "acp",
            "acp_server": "claude-code",
            "llm": {"model": "claude-sonnet-4-20250514", "usage_id": "u"},
        },
    )
    assert response.status_code == 200
    agent = response.json()["agent_settings"]
    assert agent["agent_kind"] == "acp"
    assert agent["acp_server"] == "claude-code"


def test_put_agent_invalid_body_returns_422(client):
    response = client.put(
        "/api/settings/agent",
        json={"agent_kind": "openhands", "tools": "not-a-list"},
    )
    assert response.status_code == 422


# ── PATCH / PUT /api/settings/conversation ────────────────────────────────


def test_patch_conversation_partial(client):
    client.put(
        "/api/settings/conversation",
        json={"max_iterations": 500, "confirmation_mode": False},
    )
    response = client.patch("/api/settings/conversation", json={"max_iterations": 200})
    assert response.status_code == 200
    conv = response.json()["conversation_settings"]
    assert conv["max_iterations"] == 200
    assert conv["confirmation_mode"] is False  # preserved


def test_patch_conversation_extra_field_forbidden(client):
    response = client.patch("/api/settings/conversation", json={"max_iterationz": 200})
    assert response.status_code == 422


def test_patch_conversation_validation_error_returns_422(client):
    response = client.patch("/api/settings/conversation", json={"max_iterations": 0})
    assert response.status_code == 422


def test_put_conversation_full_replace(client):
    response = client.put(
        "/api/settings/conversation",
        json={"max_iterations": 42, "confirmation_mode": True},
    )
    assert response.status_code == 200
    conv = response.json()["conversation_settings"]
    assert conv["max_iterations"] == 42
    assert conv["confirmation_mode"] is True


# ── Concurrency end-to-end ────────────────────────────────────────────────


def test_concurrent_clients_lose_update_without_if_match(client):
    """Mirrors the failure mode of the legacy API: without If-Match, the
    last writer wins silently."""
    client.put(
        "/api/settings/conversation",
        json={"max_iterations": 100, "confirmation_mode": False},
    )
    # Both "clients" capture the same view, both PATCH a different field's
    # value, neither sends If-Match — the second write clobbers the first
    # at the leaf level.
    client.patch("/api/settings/conversation", json={"max_iterations": 200})
    client.patch("/api/settings/conversation", json={"max_iterations": 300})
    final = client.get("/api/settings").json()["conversation_settings"]
    assert final["max_iterations"] == 300  # last writer wins; data loss possible


def test_concurrent_clients_detected_with_if_match(client):
    """With If-Match, the second writer's stale view is rejected with 412 —
    the lost-update is now visible and actionable."""
    client.put(
        "/api/settings/conversation",
        json={"max_iterations": 100, "confirmation_mode": False},
    )
    etag = client.get("/api/settings").headers["ETag"]

    # Client A succeeds.
    a = client.patch(
        "/api/settings/conversation",
        json={"max_iterations": 200},
        headers={"If-Match": etag},
    )
    assert a.status_code == 200

    # Client B has the stale ETag.
    b = client.patch(
        "/api/settings/conversation",
        json={"max_iterations": 300},
        headers={"If-Match": etag},
    )
    assert b.status_code == 412
    # State is still A's, not B's.
    final = client.get("/api/settings").json()["conversation_settings"]
    assert final["max_iterations"] == 200
