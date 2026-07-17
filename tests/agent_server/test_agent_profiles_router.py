"""Tests for agent_profiles_router endpoints.

Mirrors the ``test_profiles_router`` (LLM) suite, plus the AgentProfile-specific
contracts: a separate ``active_agent_profile_id`` pointer, pointer-only
activation by id (no ``agent_settings`` write), and the lazy migration seed.
"""

import concurrent.futures
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server import agent_profiles_router as router_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores
from openhands.agent_server.profiles_router import MAX_PROFILES
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.profiles import (
    ACPAgentProfile,
    AgentProfileStore,
    OpenHandsAgentProfile,
)


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
        "openhands.agent_server.agent_profiles_router.get_agent_profile_store",
        lambda: AgentProfileStore(base_dir=temp_agent_profiles_dir),
    ):
        yield TestClient(app)
    reset_stores()


@pytest.fixture
def store(temp_agent_profiles_dir):
    return AgentProfileStore(base_dir=temp_agent_profiles_dir)


@pytest.fixture
def default_llm_profile_store(temp_settings_dir):
    """The real (unpatched) LLM profile store the ``client`` fixture's
    ``get_llm_profile_store()`` resolves to, given ``OH_PERSISTENCE_DIR`` —
    see ``_get_profile_persistence_dir`` (``<dir>/profiles``)."""
    return LLMProfileStore(base_dir=temp_settings_dir / "profiles")


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


def test_seed_backfills_default_llm_profile_when_real_config(
    client, default_llm_profile_store
):
    """No active LLM profile but a *real* live LLM config: seed backfills a
    resolvable 'default' LLM profile.

    Regression test for #3933 — previously ``llm_profile_ref`` fell back to the
    literal 'default' without anything ever creating that LLM profile, so the
    seeded (and active) agent profile 404'd at conversation launch. The backfill
    fires only when the live ``agent_settings.llm`` is real, pre-existing config
    (here: a custom ``base_url``) — the legacy/cloud migration case — not for a
    fresh account's bare SDK defaults (see the ghost-profile test below, #4031).
    """
    # Give the live LLM real config so the backfill is warranted.
    client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"base_url": "https://proxy.example/v1"}}},
    )

    body = client.get("/api/agent-profiles").json()
    seeded = body["profiles"][0]
    assert seeded["llm_profile_ref"] == "default"
    assert "default.json" in default_llm_profile_store.list()

    materialized = client.post("/api/agent-profiles/default/materialize").json()
    assert materialized["valid"] is True
    assert materialized["llm_profile_resolved"] is True


def test_seed_skips_ghost_llm_profile_for_bare_defaults(
    client, default_llm_profile_store
):
    """A fresh, never-configured account must NOT get a keyless 'default' LLM
    profile persisted (#4031).

    On bare SDK defaults (``model="gpt-5.5"``, no api_key) the backfill is
    skipped: no ``default.json`` is written, so the LLM profiles list stays
    empty rather than showing an unexplained, non-functional 'ghost' profile.
    The agent profile's ``llm_profile_ref`` is left as a *soft* 'default' ref —
    which materialize reports as dangling (never raises) and which resolves for
    real the moment the user saves an actual LLM profile.
    """
    body = client.get("/api/agent-profiles").json()
    seeded = body["profiles"][0]
    # The agent profile is still seeded, with a soft (unresolved) ref.
    assert seeded["llm_profile_ref"] == "default"
    # ...but no ghost LLM profile is left behind.
    assert "default.json" not in default_llm_profile_store.list()
    assert default_llm_profile_store.list() == []

    # The soft ref surfaces as dangling at materialize time, not a 500.
    materialized = client.post("/api/agent-profiles/default/materialize").json()
    assert materialized["valid"] is False
    assert materialized["llm_profile_resolved"] is False


def test_seed_does_not_clobber_existing_default_llm_profile(
    client, default_llm_profile_store
):
    """A pre-existing 'default' LLM profile is left untouched by the seed."""
    default_llm_profile_store.save("default", LLM(model="existing/model"))

    client.get("/api/agent-profiles")

    reloaded = default_llm_profile_store.load("default")
    assert reloaded.model == "existing/model"


def test_seed_does_not_clobber_differently_cased_default_llm_profile(
    client, default_llm_profile_store
):
    """A pre-existing differently-cased 'Default' LLM profile is never
    overwritten.

    Regression test: the existence check must resolve names the same way
    ``save()`` does (via the store's own path resolution), not via a
    case-sensitive ``list()`` membership check — on a case-insensitive
    filesystem (macOS/Windows) 'default' and 'Default' are the same path, so
    the naive check would miss the collision and ``save()`` would silently
    clobber the existing profile.
    """
    default_llm_profile_store.save("Default", LLM(model="existing/model"))

    client.get("/api/agent-profiles")

    reloaded = default_llm_profile_store.load("Default")
    assert reloaded.model == "existing/model"


def test_seed_llm_profile_limit_reached_does_not_500(client, default_llm_profile_store):
    """Hitting the LLM profile cap during backfill warns and continues
    instead of 500ing.

    Regression test: the backfill must catch the LLM store's own
    ``ProfileLimitExceeded`` (``openhands.sdk.llm.llm_profile_store``), not
    the identically-named exception from the agent-profile store
    (``openhands.sdk.profiles``) — catching the wrong class let the real one
    propagate as an unhandled 500.
    """
    for i in range(MAX_PROFILES):
        default_llm_profile_store.save(f"other-{i}", LLM(model="x"))

    response = client.get("/api/agent-profiles")

    assert response.status_code == 200
    seeded = response.json()["profiles"][0]
    assert seeded["llm_profile_ref"] == "default"
    assert "default.json" not in default_llm_profile_store.list()


def test_seed_backfills_when_active_profile_is_empty_string(
    client, default_llm_profile_store, temp_settings_dir
):
    """An empty-string (not ``None``) ``active_profile`` still triggers the
    backfill.

    Regression test: the trigger condition must be a falsy check, matching
    ``build_seed_profile``'s own ``active_llm_profile or SEED_PROFILE_NAME``
    fallback — an ``is None`` check would skip the backfill for ``""`` while
    ``build_seed_profile`` still falls back to the unresolvable literal
    ``"default"`` ref, reproducing the exact #3933 dangling ref. The HTTP
    PATCH payload's pattern validator blocks a client from setting `""`, but
    the stored ``PersistedSettings`` field has no such constraint, so a
    hand-edited or legacy settings.json can still contain it.

    The live LLM carries a custom ``base_url`` so it counts as real config and
    the backfill actually persists a profile (#4031 gates it on real config).
    """
    (temp_settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "agent_settings": {
                    "agent_kind": "openhands",
                    "llm": {"model": "gpt-5.5", "base_url": "https://proxy.example/v1"},
                },
                "conversation_settings": {},
                "active_profile": "",
                "active_agent_profile_id": None,
                "misc_settings": {},
            }
        )
    )

    body = client.get("/api/agent-profiles").json()
    assert body["profiles"][0]["llm_profile_ref"] == "default"
    assert "default.json" in default_llm_profile_store.list()

    materialized = client.post("/api/agent-profiles/default/materialize").json()
    assert materialized["valid"] is True


def test_seed_acp_does_not_backfill_llm_profile(client, default_llm_profile_store):
    """An ACP seed has no ``llm_profile_ref``, so no LLM profile is created."""
    client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"agent_kind": "acp", "acp_server": "codex"}},
    )

    client.get("/api/agent-profiles")

    assert default_llm_profile_store.list() == []


@pytest.mark.parametrize(
    "llm, expected",
    [
        # Bare SDK defaults on a never-configured account -> not real config.
        (LLM(model="gpt-5.5"), False),
        (LLM(model="gpt-5.5", base_url=""), False),
        (LLM(model="gpt-5.5", base_url="   "), False),
        # A real API key.
        (LLM(model="gpt-5.5", api_key=SecretStr("sk-real")), True),
        # A blank API key does not count.
        (LLM(model="gpt-5.5", api_key=SecretStr("  ")), False),
        # A custom endpoint (proxy / self-hosted) counts.
        (LLM(model="gpt-5.5", base_url="https://proxy.example/v1"), True),
        # Subscription auth counts even without an api_key.
        (LLM(model="gpt-5.5", auth_type="subscription"), True),
    ],
)
def test_llm_has_real_config(llm, expected):
    """``_llm_has_real_config`` gates the seed so only real, pre-existing
    configuration is mirrored into a persisted 'default' LLM profile (#4031)."""
    assert router_module._llm_has_real_config(llm) is expected


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


def test_concurrent_create_same_name_converges_on_one_id(client, store):
    """Concurrent creates of the same new name yield one profile with one id.

    The save path holds the store lock across read + id-mint + write, so the
    second writer sees the namesake and preserves its id instead of clobbering
    it with a fresh one (which would dangle an active pointer).
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(
            ex.map(
                lambda _: (
                    client.post(
                        "/api/agent-profiles/dup", json={"llm_profile_ref": "x"}
                    ).status_code
                ),
                range(8),
            )
        )

    assert all(code == 201 for code in codes)
    summaries = store.list_summaries()
    assert len(summaries) == 1
    assert len({s["id"] for s in summaries}) == 1


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
    """A missing required field is rejected and the field location is surfaced.

    ``detail`` mirrors FastAPI's request-validation shape: a list of error
    objects (here trimmed to loc/type to avoid leaking secret-bearing input).
    """
    response = client.post("/api/agent-profiles/bad", json={})
    assert response.status_code == 422
    detail = response.json()["detail"]
    # The discriminated union tags the location with the variant ("openhands").
    assert any("llm_profile_ref" in err["loc"] for err in detail)


def test_save_schemaless_body_with_stray_skills_key_rejected(client):
    """Reject a removed ``skills`` field from an unversioned request."""
    response = client.post(
        "/api/agent-profiles/legacy",
        json={
            "llm_profile_ref": "base",
            "skills": [{"name": "old", "content": "x"}],
        },
    )
    assert response.status_code == 422


def test_save_current_schema_version_rejects_stray_skills_key(client):
    """A body that claims the current schema version with a stray ``skills`` key
    is a genuine extra='forbid' violation (422)."""
    from openhands.sdk.profiles.agent_profile import AGENT_PROFILE_SCHEMA_VERSION

    response = client.post(
        "/api/agent-profiles/bad",
        json={
            "schema_version": AGENT_PROFILE_SCHEMA_VERSION,
            "llm_profile_ref": "base",
            "skills": [{"name": "old", "content": "x"}],
        },
    )
    assert response.status_code == 422


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
    assert "not found" in response.json()["detail"].lower()


def test_get_ignores_expose_secrets_header(client, store):
    """A profile is secret-free at rest (#4017); GET has no ``X-Expose-Secrets``
    behavior — unlike the LLM ``/api/profiles`` router, the header is simply
    ignored rather than changing the response."""
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="base"))

    plain = client.get("/api/agent-profiles/p").json()
    encrypted = client.get(
        "/api/agent-profiles/p", headers={"X-Expose-Secrets": "encrypted"}
    ).json()
    assert plain == encrypted


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
    assert "already exists" in response.json()["detail"].lower()


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
                "enable_switch_llm_tool": False,
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
    assert prof["enable_switch_llm_tool"] is False
    assert prof["tool_concurrency_limit"] == 3
    assert prof["system_message_suffix"] == "be terse"
    # The seed disables nothing — the default profile launches with all
    # discovered skills (deny-list model).
    assert prof["disabled_skills"] == []
    assert prof["verification"]["critic_enabled"] is True
    assert prof["verification"]["critic_model_name"] == "x-critic"
    # The profile verification is secret-free — no critic_api_key projected.
    assert "critic_api_key" not in prof["verification"]


def test_seed_disables_nothing_even_with_inline_global_skills(client):
    """The seed never freezes the global's skills by name — it sets an empty
    deny-list, so the migrated default profile launches with all discovered
    skills. Freezing by name was the #4017 launch-break (an inline global skill
    absent from the launch catalog would dangle)."""
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "agent_context": {
                    "skills": [
                        {"name": "alpha", "content": "x"},
                        {"name": "beta", "content": "y"},
                    ]
                }
            }
        },
    )
    client.get("/api/agent-profiles")  # triggers the seed

    prof = client.get("/api/agent-profiles/default").json()["profile"]
    assert prof["disabled_skills"] == []


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
    # ACP profiles carry no skill-selection field at all.
    assert "skill_refs" not in prof
    assert "disabled_skills" not in prof


# ── Store errors → HTTP ─────────────────────────────────────────────────────


def test_list_timeout_returns_503(client, monkeypatch):
    def boom(self):
        raise TimeoutError("locked")

    monkeypatch.setattr(AgentProfileStore, "list", boom)
    response = client.get("/api/agent-profiles")
    assert response.status_code == 503


def test_save_timeout_returns_503(client, monkeypatch):
    def boom(self, profile, *, max_profiles=None):
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


# ── Materialize (resolve dry-run) ────────────────────────────────────────────


@pytest.fixture
def temp_llm_profiles_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        llm_dir = Path(tmpdir) / "llm-profiles"
        llm_dir.mkdir(parents=True, exist_ok=True)
        yield llm_dir


@pytest.fixture
def client_with_llm_store(
    temp_agent_profiles_dir, temp_settings_dir, temp_llm_profiles_dir, monkeypatch
):
    """Test client with isolated agent-profile/settings/llm-profile dirs, no cipher."""
    reset_stores()
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_settings_dir))
    config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
    app = create_app(config)
    with (
        patch(
            "openhands.agent_server.agent_profiles_router.get_agent_profile_store",
            lambda: AgentProfileStore(base_dir=temp_agent_profiles_dir),
        ),
        patch(
            "openhands.agent_server.agent_profiles_router.get_llm_profile_store",
            lambda: LLMProfileStore(base_dir=temp_llm_profiles_dir),
        ),
    ):
        yield TestClient(app)
    reset_stores()


@pytest.fixture
def llm_store(temp_llm_profiles_dir):
    return LLMProfileStore(base_dir=temp_llm_profiles_dir)


def test_materialize_valid_openhands_profile(client_with_llm_store, store, llm_store):
    """Valid OpenHands profile with a resolved LLM returns 200 + valid=True."""
    llm_store.save("base-llm", LLM(model="gpt-4o"), include_secrets=True)
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="base-llm"))

    response = client_with_llm_store.post("/api/agent-profiles/p/materialize")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["agent_kind"] == "openhands"
    assert body["llm_profile_ref"] == "base-llm"
    assert body["llm_profile_resolved"] is True
    assert body["errors"] == []
    assert body["resolved_settings"] is not None
    assert body["dangling_mcp_server_refs"] == []


def test_materialize_valid_acp_profile(client_with_llm_store, store):
    """Valid ACP profile returns 200 + valid=True (no LLM ref needed)."""
    store.save(ACPAgentProfile(name="acp-p", acp_server="codex", acp_model="gpt-5.5"))

    response = client_with_llm_store.post("/api/agent-profiles/acp-p/materialize")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["agent_kind"] == "acp"
    assert body["errors"] == []
    assert body["resolved_settings"] is not None


def test_materialize_dangling_llm_ref(client_with_llm_store, store):
    """A profile referencing a missing LLM profile returns 200, valid=False."""
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="nonexistent"))

    response = client_with_llm_store.post("/api/agent-profiles/p/materialize")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["llm_profile_ref"] == "nonexistent"
    assert body["llm_profile_resolved"] is False
    assert body["resolved_settings"] is None
    assert any("nonexistent" in e for e in body["errors"])


def test_materialize_dangling_mcp_ref(client_with_llm_store, store, llm_store):
    """A profile with a missing MCP server ref returns 200, valid=False."""
    llm_store.save("base-llm", LLM(model="gpt-4o"), include_secrets=True)
    store.save(
        OpenHandsAgentProfile(
            name="p",
            llm_profile_ref="base-llm",
            mcp_server_refs=["missing-server"],
        )
    )

    response = client_with_llm_store.post("/api/agent-profiles/p/materialize")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["dangling_mcp_server_refs"] == ["missing-server"]
    assert body["resolved_settings"] is None


def test_materialize_reports_disabled_and_resolved_skills(
    client_with_llm_store, store, llm_store
):
    """The materialize dry-run reports the deny-list and the resolved set
    (catalog minus disabled). A disabled name absent from the catalog is a no-op
    and does NOT invalidate the profile — the deny-list can't dangle (#4017)."""
    from openhands.sdk.skills import Skill

    llm_store.save("base-llm", LLM(model="gpt-4o"), include_secrets=True)
    store.save(
        OpenHandsAgentProfile(
            name="p",
            llm_profile_ref="base-llm",
            disabled_skills=["beta", "not-in-catalog"],
        )
    )

    with patch(
        "openhands.agent_server.agent_profiles_router.discover_profile_skills",
        return_value=[
            Skill(name="alpha", content="x"),
            Skill(name="beta", content="y"),
        ],
    ):
        response = client_with_llm_store.post("/api/agent-profiles/p/materialize")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["disabled_skills"] == ["beta", "not-in-catalog"]
    assert body["resolved_skills"] == ["alpha"]
    assert body["resolved_settings"] is not None


def test_materialize_unknown_name_returns_404(client_with_llm_store):
    """Materializing an unknown profile name returns 404."""
    response = client_with_llm_store.post("/api/agent-profiles/ghost/materialize")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_materialize_no_raw_secrets_in_resolved_settings(
    client_with_llm_store, store, llm_store
):
    """resolved_settings must not contain raw API key values."""
    raw_key = "sk-secret-key-should-not-appear"

    llm_store.save(
        "base-llm",
        LLM(model="gpt-4o", api_key=SecretStr(raw_key)),
        include_secrets=True,
    )
    store.save(OpenHandsAgentProfile(name="p", llm_profile_ref="base-llm"))

    response = client_with_llm_store.post("/api/agent-profiles/p/materialize")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert raw_key not in response.text
