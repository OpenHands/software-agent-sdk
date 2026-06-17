"""Tests for agent_profiles_router endpoints.

Mirrors the ``test_profiles_router`` (LLM) suite, plus the AgentProfile-specific
contracts: a separate ``active_agent_profile_id`` pointer, pointer-only
activation by id (no ``agent_settings`` write), and the lazy migration seed.
"""

import concurrent.futures
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import agent_profiles_router as router_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores
from openhands.sdk.profiles import AgentProfileStore, OpenHandsAgentProfile


@pytest.fixture
def temp_agent_profiles_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent_dir = Path(tmpdir) / "agent-profiles"
        agent_dir.mkdir(parents=True, exist_ok=True)
        yield agent_dir


@pytest.fixture
def temp_settings_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_dir = Path(tmpdir) / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        yield settings_dir


@pytest.fixture
def client(temp_agent_profiles_dir, temp_settings_dir, monkeypatch):
    """Test client with isolated agent-profile/settings dirs, no cipher."""
    reset_stores()
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_settings_dir))
    config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
    app = create_app(config)
    with patch(
        "openhands.agent_server.agent_profiles_router.AgentProfileStore",
        lambda: AgentProfileStore(base_dir=temp_agent_profiles_dir),
    ):
        yield TestClient(app)
    reset_stores()


@pytest.fixture
def store(temp_agent_profiles_dir):
    return AgentProfileStore(base_dir=temp_agent_profiles_dir)


# ── Lazy migration seed ─────────────────────────────────────────────────────


def test_first_list_seeds_default_profile(client):
    """First GET on an empty store seeds exactly one default profile."""
    response = client.get("/api/agent-profiles")

    assert response.status_code == 200
    body = response.json()
    assert len(body["profiles"]) == 1
    seeded = body["profiles"][0]
    assert seeded["name"] == "default"
    assert seeded["agent_kind"] == "openhands"
    assert seeded["llm_profile_ref"] == "default"
    assert seeded["mcp_server_refs"] is None
    # The active pointer is set to the seeded profile's id.
    assert body["active_agent_profile_id"] == seeded["id"]

    # And it is persisted into settings.
    settings = client.get("/api/settings").json()
    assert settings["active_agent_profile_id"] == seeded["id"]


def test_seed_is_idempotent(client):
    """A second GET does not seed again."""
    first = client.get("/api/agent-profiles").json()
    second = client.get("/api/agent-profiles").json()

    assert len(second["profiles"]) == 1
    assert second["active_agent_profile_id"] == first["active_agent_profile_id"]


def test_seed_references_active_llm_profile(client):
    """The seed references the active LLM profile when one is set."""
    client.patch("/api/settings", json={"active_profile": "my-llm"})

    body = client.get("/api/agent-profiles").json()
    assert body["profiles"][0]["llm_profile_ref"] == "my-llm"


def test_seed_acp_when_settings_acp(client):
    """ACP agent_settings seed an ACP profile (no llm_profile_ref)."""
    client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"agent_kind": "acp", "acp_server": "codex"}},
    )

    body = client.get("/api/agent-profiles").json()
    seeded = body["profiles"][0]
    assert seeded["agent_kind"] == "acp"
    assert seeded["llm_profile_ref"] is None

    detail = client.get("/api/agent-profiles/default").json()
    assert detail["profile"]["acp_server"] == "codex"


def test_no_seed_when_store_nonempty(client, store):
    """A non-empty store is never seeded."""
    store.save(OpenHandsAgentProfile(name="mine", llm_profile_ref="x"))

    body = client.get("/api/agent-profiles").json()
    names = {p["name"] for p in body["profiles"]}
    assert names == {"mine"}
    assert body["active_agent_profile_id"] is None


def test_no_seed_when_pointer_set_but_store_empty(client):
    """An empty store with a non-null pointer is left as-is (no seed, no error).

    A stale pointer (e.g. after a failed delete) reflects user state, so the
    seed condition deliberately requires both an empty store *and* a null
    pointer.
    """
    stale = "12345678-1234-1234-1234-1234567890ab"
    client.patch("/api/settings", json={"active_agent_profile_id": stale})

    body = client.get("/api/agent-profiles").json()
    assert body["profiles"] == []
    assert body["active_agent_profile_id"] == stale


def test_concurrent_first_list_seeds_once(client, store):
    """Concurrent first GETs seed exactly one profile; the pointer is consistent.

    The seed holds the store lock across check + save + pointer write, so the
    losing requests see a non-empty store and the active pointer always matches
    the single persisted profile id (never a dangling/overwritten id).
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(
            ex.map(lambda _: client.get("/api/agent-profiles").status_code, range(8))
        )

    assert all(code == 200 for code in codes)
    summaries = store.list_summaries()
    assert len(summaries) == 1  # seeded exactly once
    pointer = client.get("/api/settings").json()["active_agent_profile_id"]
    assert pointer == summaries[0]["id"]  # pointer resolves to the real profile


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_save_creates_new(client, store):
    response = client.post(
        "/api/agent-profiles/new-profile",
        json={"llm_profile_ref": "base-llm"},
    )

    assert response.status_code == 201
    assert "saved" in response.json()["message"].lower()
    loaded = store.load("new-profile")
    assert loaded.llm_profile_ref == "base-llm"


def test_save_overwrites_existing(client, store):
    store.save(OpenHandsAgentProfile(name="existing", llm_profile_ref="old"))

    response = client.post(
        "/api/agent-profiles/existing",
        json={"llm_profile_ref": "new"},
    )

    assert response.status_code == 201
    assert store.load("existing").llm_profile_ref == "new"


def test_overwrite_preserves_id_and_pointer(client, store):
    """Overwriting a profile keeps its id stable (and bumps revision).

    A create-style body that omits ``id``/``revision`` must not mint a fresh
    UUID — that would dangle the active pointer keyed on the old id.
    """
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="base"))
    pid = client.get("/api/agent-profiles/p").json()["profile"]["id"]
    client.post(f"/api/agent-profiles/{pid}/activate")
    assert client.get("/api/settings").json()["active_agent_profile_id"] == pid

    response = client.post("/api/agent-profiles/p", json={"llm_profile_ref": "changed"})
    assert response.status_code == 201

    detail = client.get("/api/agent-profiles/p").json()["profile"]
    assert detail["id"] == pid  # stable id preserved
    assert detail["revision"] == 1  # monotonically bumped
    assert detail["llm_profile_ref"] == "changed"
    # The active pointer still resolves to the (same-id) profile.
    assert client.get("/api/settings").json()["active_agent_profile_id"] == pid


def test_create_mints_fresh_id_ignoring_client_id(client):
    """Creating a new name never reuses a client-supplied id (ids stay unique).

    Duplicate ids would make the id-keyed active pointer ambiguous — deleting
    one profile could clear the active selection while a namesake id lives on.
    """
    client.post("/api/agent-profiles/a", json={"llm_profile_ref": "x"})
    a_id = client.get("/api/agent-profiles/a").json()["profile"]["id"]

    # Try to create 'b' reusing a's id; the server must mint a fresh one.
    client.post("/api/agent-profiles/b", json={"llm_profile_ref": "y", "id": a_id})
    b_id = client.get("/api/agent-profiles/b").json()["profile"]["id"]
    assert b_id != a_id

    # Activate b, delete a: the pointer must survive (ids are distinct).
    client.post(f"/api/agent-profiles/{b_id}/activate")
    client.delete("/api/agent-profiles/a")
    assert client.get("/api/settings").json()["active_agent_profile_id"] == b_id


def test_save_path_name_is_authoritative(client, store):
    """The path name overrides any ``name`` in the body."""
    response = client.post(
        "/api/agent-profiles/path-name",
        json={"name": "body-name", "llm_profile_ref": "x"},
    )

    assert response.status_code == 201
    assert store.load("path-name").name == "path-name"
    with pytest.raises(FileNotFoundError):
        store.load("body-name")


def test_save_acp_profile(client, store):
    response = client.post(
        "/api/agent-profiles/acp-one",
        json={"agent_kind": "acp", "acp_server": "codex", "acp_model": "gpt-5.5"},
    )

    assert response.status_code == 201
    loaded = store.load("acp-one")
    assert loaded.agent_kind == "acp"
    assert loaded.acp_server == "codex"


def test_save_missing_required_ref_returns_422(client):
    """A missing required field is rejected and the field location is surfaced."""
    response = client.post("/api/agent-profiles/bad", json={})
    assert response.status_code == 422
    detail = response.json()["detail"]
    # The discriminated union tags the location with the variant ("openhands").
    assert any("llm_profile_ref" in err["loc"] for err in detail["errors"])


def test_save_invalid_body_does_not_leak_mcp_secret(client):
    """A malformed profile body's 422 must not echo skills[].mcp_tools secrets."""
    secret = "ghp_should_never_appear_in_error"
    response = client.post(
        "/api/agent-profiles/bad",
        json={
            "llm_profile_ref": "base",
            "skills": [
                {
                    "name": "leaky",
                    "content": "x",
                    # Malformed: mcpServers must be an object, forcing a failure.
                    "mcp_tools": {"mcpServers": {"svc": {"headers": secret}}},
                }
            ],
        },
    )
    assert response.status_code == 422
    assert secret not in response.text


def test_save_extra_field_returns_422(client):
    """extra='forbid' rejects unknown fields."""
    response = client.post(
        "/api/agent-profiles/bad",
        json={"llm_profile_ref": "x", "bogus": 1},
    )
    assert response.status_code == 422


def test_save_invalid_name_returns_422(client):
    response = client.post(
        "/api/agent-profiles/.hidden",
        json={"llm_profile_ref": "x"},
    )
    assert response.status_code in (400, 404, 422)


def test_get_returns_profile(client, store):
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="base"))

    response = client.get("/api/agent-profiles/p")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "p"
    assert body["profile"]["llm_profile_ref"] == "base"
    assert body["profile"]["agent_kind"] == "openhands"


def test_get_not_found(client):
    response = client.get("/api/agent-profiles/nonexistent")
    assert response.status_code == 404


def test_get_corrupted_returns_400(client, temp_agent_profiles_dir):
    (temp_agent_profiles_dir / "broken.json").write_text("{ not valid json")
    response = client.get("/api/agent-profiles/broken")
    assert response.status_code == 400


def test_delete_removes_existing(client, store):
    store.save(OpenHandsAgentProfile(name="to-delete", llm_profile_ref="x"))

    response = client.delete("/api/agent-profiles/to-delete")

    assert response.status_code == 200
    with pytest.raises(FileNotFoundError):
        store.load("to-delete")


def test_delete_idempotent(client):
    response = client.delete("/api/agent-profiles/nonexistent")
    assert response.status_code == 200


def test_delete_clears_active_pointer(client, store):
    """Deleting the active profile clears active_agent_profile_id."""
    store.save(OpenHandsAgentProfile(name="active-one", llm_profile_ref="x"))
    profile_id = client.get("/api/agent-profiles/active-one").json()["profile"]["id"]
    client.post(f"/api/agent-profiles/{profile_id}/activate")
    assert client.get("/api/settings").json()["active_agent_profile_id"] == profile_id

    client.delete("/api/agent-profiles/active-one")

    assert client.get("/api/settings").json()["active_agent_profile_id"] is None


def test_rename_success(client, store):
    store.save(OpenHandsAgentProfile(name="old-name", llm_profile_ref="x"))

    response = client.post(
        "/api/agent-profiles/old-name/rename",
        json={"new_name": "new-name"},
    )

    assert response.status_code == 200
    assert "renamed" in response.json()["message"].lower()
    with pytest.raises(FileNotFoundError):
        store.load("old-name")
    assert store.load("new-name").llm_profile_ref == "x"


def test_rename_not_found(client):
    response = client.post(
        "/api/agent-profiles/ghost/rename",
        json={"new_name": "new-name"},
    )
    assert response.status_code == 404


def test_rename_conflict(client, store):
    store.save(OpenHandsAgentProfile(name="source", llm_profile_ref="a"))
    store.save(OpenHandsAgentProfile(name="target", llm_profile_ref="b"))

    response = client.post(
        "/api/agent-profiles/source/rename",
        json={"new_name": "target"},
    )
    assert response.status_code == 409


def test_rename_invalid_new_name_returns_422(client, store):
    store.save(OpenHandsAgentProfile(name="valid", llm_profile_ref="x"))
    response = client.post(
        "/api/agent-profiles/valid/rename",
        json={"new_name": "../etc/passwd"},
    )
    assert response.status_code == 422


def test_rename_preserves_active_pointer(client, store):
    """The id-keyed active pointer survives a rename (id is stable)."""
    store.save(OpenHandsAgentProfile(name="before", llm_profile_ref="x"))
    profile_id = client.get("/api/agent-profiles/before").json()["profile"]["id"]
    client.post(f"/api/agent-profiles/{profile_id}/activate")

    client.post("/api/agent-profiles/before/rename", json={"new_name": "after"})

    # Same id, still active.
    assert client.get("/api/settings").json()["active_agent_profile_id"] == profile_id
    assert client.get("/api/agent-profiles/after").json()["profile"]["id"] == profile_id


# ── Activate (pointer only, by id) ──────────────────────────────────────────


def test_activate_sets_pointer_without_mutating_agent_settings(client, store):
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="x"))
    # Persist settings once first so the snapshot is already round-tripped
    # (the default un-persisted vs persisted form differs harmlessly).
    client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )
    before = client.get("/api/settings").json()["agent_settings"]
    profile_id = client.get("/api/agent-profiles/p").json()["profile"]["id"]

    response = client.post(f"/api/agent-profiles/{profile_id}/activate")

    assert response.status_code == 200
    assert response.json()["agent_settings_applied"] is False
    after = client.get("/api/settings").json()
    assert after["active_agent_profile_id"] == profile_id
    # agent_settings is untouched — the creation-time-only contract.
    assert after["agent_settings"] == before


def test_activate_unknown_id_returns_404(client, store):
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="x"))
    unknown = "00000000-dead-beef-0000-000000000000"
    response = client.post(f"/api/agent-profiles/{unknown}/activate")
    assert response.status_code == 404


def test_activate_settings_corruption_returns_500(client, store, monkeypatch):
    """A corrupted/mis-keyed settings file is a server-side failure (500)."""
    from openhands.agent_server.persistence.store import FileSettingsStore

    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="x"))
    profile_id = client.get("/api/agent-profiles/p").json()["profile"]["id"]

    def boom(self, *args, **kwargs):
        raise RuntimeError("settings file corrupted")

    monkeypatch.setattr(FileSettingsStore, "update", boom)
    response = client.post(f"/api/agent-profiles/{profile_id}/activate")
    assert response.status_code == 500


# ── Seed fidelity (migration preserves the user's launch config) ────────────


def test_seed_preserves_openhands_fields(client):
    """The OpenHands seed carries the overlapping launch fields, not just refs."""
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "enable_sub_agents": True,
                "tool_concurrency_limit": 3,
                "agent_context": {"system_message_suffix": "be terse"},
                "verification": {
                    "critic_enabled": True,
                    "critic_model_name": "x-critic",
                },
            }
        },
    )
    client.get("/api/agent-profiles")  # triggers the seed

    prof = client.get("/api/agent-profiles/default").json()["profile"]
    assert prof["enable_sub_agents"] is True
    assert prof["tool_concurrency_limit"] == 3
    assert prof["system_message_suffix"] == "be terse"
    assert prof["verification"]["critic_enabled"] is True
    assert prof["verification"]["critic_model_name"] == "x-critic"
    # The profile verification is secret-free — no critic_api_key projected.
    assert "critic_api_key" not in prof["verification"]


def test_seed_preserves_acp_fields(client):
    """The ACP seed carries acp_server/model/args, not just the kind."""
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "agent_kind": "acp",
                "acp_server": "codex",
                "acp_model": "gpt-5.5",
                "acp_args": ["--foo", "--bar"],
            }
        },
    )
    client.get("/api/agent-profiles")  # triggers the seed

    prof = client.get("/api/agent-profiles/default").json()["profile"]
    assert prof["agent_kind"] == "acp"
    assert prof["acp_server"] == "codex"
    assert prof["acp_model"] == "gpt-5.5"
    assert prof["acp_args"] == ["--foo", "--bar"]


# ── Cipher: skills[].mcp_tools secret round-trip ────────────────────────────


@pytest.fixture
def secret_key():
    from base64 import urlsafe_b64encode

    return urlsafe_b64encode(b"a" * 32).decode("ascii")


@pytest.fixture
def cipher(secret_key):
    from openhands.sdk.utils.cipher import Cipher

    return Cipher(secret_key)


@pytest.fixture
def client_with_cipher(
    temp_agent_profiles_dir, temp_settings_dir, secret_key, monkeypatch
):
    from pydantic import SecretStr

    reset_stores()
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_settings_dir))
    config = Config(
        static_files_path=None, session_api_keys=[], secret_key=SecretStr(secret_key)
    )
    app = create_app(config)
    with patch(
        "openhands.agent_server.agent_profiles_router.AgentProfileStore",
        lambda: AgentProfileStore(base_dir=temp_agent_profiles_dir),
    ):
        yield TestClient(app)
    reset_stores()


def _profile_with_mcp_secret(header_value: str) -> dict:
    return {
        "llm_profile_ref": "base",
        "skills": [
            {
                "name": "leaky",
                "content": "do stuff",
                "mcp_tools": {
                    "mcpServers": {
                        "svc": {
                            "url": "https://x.test",
                            "headers": {"Authorization": header_value},
                        }
                    }
                },
            }
        ],
    }


def _mcp_auth(profile_payload: dict) -> str:
    servers = profile_payload["skills"][0]["mcp_tools"]["mcpServers"]
    return servers["svc"]["headers"]["Authorization"]


def test_mcp_tools_secret_encrypted_roundtrip(client_with_cipher, cipher):
    """GET(encrypted) -> POST -> the secret still decrypts exactly once.

    Without decrypt-incoming-before-save the re-posted token would be encrypted
    again and the stored value would decrypt to a stale token.
    """
    secret = "Bearer ghp_roundtrip_secret"

    created = client_with_cipher.post(
        "/api/agent-profiles/p", json=_profile_with_mcp_secret(secret)
    )
    assert created.status_code == 201

    # GET encrypted: a Fernet token of the ORIGINAL secret (not double-encrypted).
    enc = client_with_cipher.get(
        "/api/agent-profiles/p", headers={"X-Expose-Secrets": "encrypted"}
    ).json()
    token = _mcp_auth(enc["profile"])
    assert token != secret
    assert cipher.decrypt(token).get_secret_value() == secret

    # Re-post the encrypted token (an ordinary client edit round-trip).
    reposted = client_with_cipher.post(
        "/api/agent-profiles/p", json=_profile_with_mcp_secret(token)
    )
    assert reposted.status_code == 201

    # Plaintext GET returns the original secret -> decrypted exactly once.
    plain = client_with_cipher.get(
        "/api/agent-profiles/p", headers={"X-Expose-Secrets": "plaintext"}
    ).json()
    assert _mcp_auth(plain["profile"]) == secret


def test_mcp_tools_secret_encrypted_at_rest(
    client_with_cipher, temp_agent_profiles_dir, cipher
):
    """A posted plaintext MCP secret is encrypted on disk, never stored raw."""
    import json

    secret = "Bearer ghp_at_rest_secret"
    client_with_cipher.post(
        "/api/agent-profiles/p", json=_profile_with_mcp_secret(secret)
    )

    raw = json.loads((temp_agent_profiles_dir / "p.json").read_text())
    stored = raw["skills"][0]["mcp_tools"]["mcpServers"]["svc"]["headers"][
        "Authorization"
    ]
    assert stored != secret
    assert cipher.decrypt(stored).get_secret_value() == secret


# ── Store errors → HTTP ─────────────────────────────────────────────────────


def test_list_timeout_returns_503(client, monkeypatch):
    def boom(self):
        raise TimeoutError("locked")

    monkeypatch.setattr(AgentProfileStore, "list", boom)
    response = client.get("/api/agent-profiles")
    assert response.status_code == 503


def test_save_timeout_returns_503(client, monkeypatch):
    def boom(self, profile, *, cipher=None, max_profiles=None):
        raise TimeoutError("locked")

    monkeypatch.setattr(AgentProfileStore, "save", boom)
    response = client.post("/api/agent-profiles/x", json={"llm_profile_ref": "y"})
    assert response.status_code == 503


def test_save_at_limit_returns_409(client, store, monkeypatch):
    monkeypatch.setattr(router_module, "MAX_AGENT_PROFILES", 1)
    store.save(OpenHandsAgentProfile(name="first", llm_profile_ref="x"))

    response = client.post("/api/agent-profiles/second", json={"llm_profile_ref": "y"})
    assert response.status_code == 409
    assert "limit" in response.json()["detail"].lower()
