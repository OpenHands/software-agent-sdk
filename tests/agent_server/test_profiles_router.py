"""Tests for profiles_router endpoints."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import profiles_router as profiles_router_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


@pytest.fixture
def temp_profiles_dir():
    """Create a temporary directory for profiles."""
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles_dir = Path(tmpdir) / "profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        yield profiles_dir


@pytest.fixture
def client(temp_profiles_dir):
    """Create test client with isolated profiles directory."""
    config = Config(static_files_path=None, session_api_keys=[])
    app = create_app(config)

    # Patch LLMProfileStore to use temp directory
    with patch(
        "openhands.agent_server.profiles_router.LLMProfileStore",
        lambda: LLMProfileStore(base_dir=temp_profiles_dir),
    ):
        yield TestClient(app)


@pytest.fixture
def store(temp_profiles_dir):
    """Create a profile store using the temp directory."""
    return LLMProfileStore(base_dir=temp_profiles_dir)


# ── List Profiles ──────────────────────────────────────────────────────────


def test_list_profiles_empty(client):
    """GET /api/profiles returns empty list when no profiles exist."""
    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    assert body["profiles"] == []


def test_list_profiles_returns_saved_profiles(client, store):
    """GET /api/profiles returns all saved profiles with model info."""
    # Save some profiles directly via store
    llm1 = LLM(model="gpt-4o")
    llm2 = LLM(model="claude-3-opus", api_key="sk-test-key")
    store.save("profile-a", llm1)
    store.save("profile-b", llm2, include_secrets=True)

    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    profiles = body["profiles"]
    assert len(profiles) == 2

    names = {p["name"] for p in profiles}
    assert names == {"profile-a", "profile-b"}

    # Check profile details
    profile_a = next(p for p in profiles if p["name"] == "profile-a")
    assert profile_a["model"] == "gpt-4o"
    assert profile_a["api_key_set"] is False

    profile_b = next(p for p in profiles if p["name"] == "profile-b")
    assert profile_b["model"] == "claude-3-opus"
    assert profile_b["api_key_set"] is True


# ── Get Profile ────────────────────────────────────────────────────────────


def test_get_profile_returns_config(client, store):
    """GET /api/profiles/{name} returns profile config with api_key nulled."""
    llm = LLM(model="gpt-4o", api_key="sk-secret-key", temperature=0.7)
    store.save("my-profile", llm, include_secrets=True)

    response = client.get("/api/profiles/my-profile")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "my-profile"
    assert body["config"]["model"] == "gpt-4o"
    assert body["config"]["temperature"] == 0.7
    assert body["config"]["api_key"] is None  # Never exposed
    assert body["api_key_set"] is True


def test_get_profile_not_found(client):
    """GET /api/profiles/{name} returns 404 for non-existent profile."""
    response = client.get("/api/profiles/nonexistent")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_profile_invalid_name(client):
    """GET /api/profiles/{name} rejects invalid profile names."""
    # Path traversal attempt - may be 404 (decoded and treated as not found)
    # or 422 (validation error) depending on how the path is parsed
    response = client.get("/api/profiles/..%2Fetc%2Fpasswd")
    assert response.status_code in (404, 422)

    # Hidden file attempt
    response = client.get("/api/profiles/.hidden")
    assert response.status_code in (400, 404, 422)


# ── Save Profile ───────────────────────────────────────────────────────────


def test_save_profile_creates_new(client, store):
    """POST /api/profiles/{name} creates a new profile."""
    response = client.post(
        "/api/profiles/new-profile",
        json={
            "llm": {"model": "gpt-4o", "api_key": "sk-test-key"},
            "include_secrets": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "new-profile"
    assert "saved" in body["message"].lower()

    # Verify profile was saved
    loaded = store.load("new-profile")
    assert loaded.model == "gpt-4o"


def test_save_profile_overwrites_existing(client, store):
    """POST /api/profiles/{name} overwrites existing profile."""
    # Save initial profile
    llm1 = LLM(model="gpt-4o")
    store.save("existing", llm1)

    # Overwrite with new config
    response = client.post(
        "/api/profiles/existing",
        json={"llm": {"model": "claude-3-opus"}},
    )

    assert response.status_code == 201

    # Verify overwritten
    loaded = store.load("existing")
    assert loaded.model == "claude-3-opus"


def test_save_profile_without_secrets(client, store):
    """POST /api/profiles/{name} with include_secrets=False omits api_key."""
    response = client.post(
        "/api/profiles/no-secrets",
        json={
            "llm": {"model": "gpt-4o", "api_key": "sk-should-not-save"},
            "include_secrets": False,
        },
    )

    assert response.status_code == 201

    # Verify api_key was not saved
    loaded = store.load("no-secrets")
    assert loaded.api_key is None or loaded.api_key.get_secret_value() == ""


def test_save_profile_invalid_name(client):
    """POST /api/profiles/{name} returns 422 for invalid names."""
    response = client.post(
        "/api/profiles/invalid/name",
        json={"llm": {"model": "gpt-4o"}},
    )
    # Should fail at path validation or be treated as different route
    assert response.status_code in (404, 422)


# ── Delete Profile ─────────────────────────────────────────────────────────


def test_delete_profile_removes_existing(client, store):
    """DELETE /api/profiles/{name} removes the profile."""
    llm = LLM(model="gpt-4o")
    store.save("to-delete", llm)

    response = client.delete("/api/profiles/to-delete")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "to-delete"
    assert "deleted" in body["message"].lower()

    # Verify deleted
    with pytest.raises(FileNotFoundError):
        store.load("to-delete")


def test_delete_profile_idempotent(client):
    """DELETE /api/profiles/{name} succeeds even for non-existent profile."""
    response = client.delete("/api/profiles/nonexistent")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "nonexistent"


# ── Rename Profile ─────────────────────────────────────────────────────────


def test_rename_profile_success(client, store):
    """POST /api/profiles/{name}/rename renames the profile."""
    llm = LLM(model="gpt-4o", api_key="sk-secret")
    store.save("old-name", llm, include_secrets=True)

    response = client.post(
        "/api/profiles/old-name/rename",
        json={"new_name": "new-name"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "new-name"
    assert "renamed" in body["message"].lower()

    # Verify old gone, new exists with same config
    with pytest.raises(FileNotFoundError):
        store.load("old-name")

    loaded = store.load("new-name")
    assert loaded.model == "gpt-4o"


def test_rename_profile_preserves_secrets(client, store):
    """POST /api/profiles/{name}/rename preserves api_key."""
    llm = LLM(model="gpt-4o", api_key="sk-secret-preserve")
    store.save("with-secret", llm, include_secrets=True)

    response = client.post(
        "/api/profiles/with-secret/rename",
        json={"new_name": "renamed-secret"},
    )

    assert response.status_code == 200

    # Verify secret preserved
    loaded = store.load("renamed-secret")
    assert loaded.api_key is not None
    assert loaded.api_key.get_secret_value() == "sk-secret-preserve"


def test_rename_profile_not_found(client):
    """POST /api/profiles/{name}/rename returns 404 for non-existent profile."""
    response = client.post(
        "/api/profiles/nonexistent/rename",
        json={"new_name": "new-name"},
    )

    assert response.status_code == 404


def test_rename_profile_conflict(client, store):
    """POST /api/profiles/{name}/rename returns 409 if new_name exists."""
    llm1 = LLM(model="gpt-4o")
    llm2 = LLM(model="claude-3-opus")
    store.save("source", llm1)
    store.save("target", llm2)

    response = client.post(
        "/api/profiles/source/rename",
        json={"new_name": "target"},
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_rename_profile_same_name(client, store):
    """POST /api/profiles/{name}/rename with same name is a no-op."""
    llm = LLM(model="gpt-4o")
    store.save("same-name", llm)

    response = client.post(
        "/api/profiles/same-name/rename",
        json={"new_name": "same-name"},
    )

    assert response.status_code == 200
    assert "unchanged" in response.json()["message"].lower()


def test_rename_profile_same_name_missing_returns_404(client):
    """Same-name rename of a missing profile must return 404, not 200."""
    response = client.post(
        "/api/profiles/ghost/rename",
        json={"new_name": "ghost"},
    )
    assert response.status_code == 404


def test_rename_profile_invalid_new_name(client, store):
    """POST /api/profiles/{name}/rename returns 422 for invalid new_name."""
    llm = LLM(model="gpt-4o")
    store.save("valid-name", llm)

    response = client.post(
        "/api/profiles/valid-name/rename",
        json={"new_name": "../etc/passwd"},
    )

    assert response.status_code == 422


# ── Profile Name Validation ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "simple",
        "with-dash",
        "with_underscore",
        "with.dot",
        "MixedCase123",
        "a" * 64,  # Max length
    ],
)
def test_valid_profile_names(client, name):
    """Valid profile names are accepted."""
    response = client.post(
        f"/api/profiles/{name}",
        json={"llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 201


def test_invalid_profile_name_too_long(client):
    """Profile name that is too long is rejected."""
    name = "a" * 65  # Exceeds 64 char limit
    response = client.post(
        f"/api/profiles/{name}",
        json={"llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("name", [".leading-dot", "-leading-dash", "_leading_under"])
def test_invalid_profile_name_leading_non_alnum(client, name):
    """Profile names must start with an alphanumeric character."""
    response = client.post(
        f"/api/profiles/{name}",
        json={"llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("name", ["name@symbol", "name$dollar", "name space"])
def test_invalid_profile_name_special_chars(client, name):
    """Profile names with disallowed characters are rejected."""
    response = client.post(
        f"/api/profiles/{name}",
        json={"llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 422


# ── Profile Limit ──────────────────────────────────────────────────────────


def test_save_profile_at_limit_returns_409(client, store, monkeypatch):
    """POST /api/profiles/{name} returns 409 when MAX_PROFILES is reached."""
    monkeypatch.setattr(profiles_router_module, "MAX_PROFILES", 2)

    store.save("first", LLM(model="gpt-4o"))
    store.save("second", LLM(model="gpt-4o"))

    response = client.post(
        "/api/profiles/third",
        json={"llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 409
    assert "limit" in response.json()["detail"].lower()


def test_save_profile_at_limit_overwrite_allowed(client, store, monkeypatch):
    """Overwriting an existing profile is allowed even at the limit."""
    monkeypatch.setattr(profiles_router_module, "MAX_PROFILES", 2)

    store.save("first", LLM(model="gpt-4o"))
    store.save("second", LLM(model="gpt-4o"))

    response = client.post(
        "/api/profiles/first",
        json={"llm": {"model": "claude-3-opus"}},
    )
    assert response.status_code == 201
    assert store.load("first").model == "claude-3-opus"


# ── Store Errors → HTTP ────────────────────────────────────────────────────


def test_list_profiles_timeout_returns_503(client, monkeypatch):
    """List endpoint surfaces TimeoutError as 503."""

    def boom(self):
        raise TimeoutError("locked")

    monkeypatch.setattr(LLMProfileStore, "list_summaries", boom)

    response = client.get("/api/profiles")
    assert response.status_code == 503


def test_get_profile_timeout_returns_503(client, store, monkeypatch):
    """Get endpoint surfaces TimeoutError as 503."""
    store.save("present", LLM(model="gpt-4o"))

    def boom(self, name):
        raise TimeoutError("locked")

    monkeypatch.setattr(LLMProfileStore, "load", boom)

    response = client.get("/api/profiles/present")
    assert response.status_code == 503


def test_delete_profile_invalid_internal_name_returns_400(client, store, monkeypatch):
    """If the store raises ValueError, delete responds 400 instead of 500."""

    def boom(self, name):
        raise ValueError("Invalid profile name: 'x'.")

    monkeypatch.setattr(LLMProfileStore, "delete", boom)

    response = client.delete("/api/profiles/some-name")
    assert response.status_code == 400


def test_list_profiles_skips_corrupted(client, temp_profiles_dir):
    """Corrupted profile files are skipped, not returned."""
    (temp_profiles_dir / "good.json").write_text('{"model": "gpt-4o"}')
    (temp_profiles_dir / "bad.json").write_text("{ not valid json")

    response = client.get("/api/profiles")
    assert response.status_code == 200

    names = {p["name"] for p in response.json()["profiles"]}
    assert names == {"good"}


def test_list_profiles_api_key_set_for_redacted(client, store):
    """A profile saved without secrets reports api_key_set=False."""
    llm = LLM(model="gpt-4o", api_key="sk-secret-not-saved")
    store.save("redacted", llm, include_secrets=False)

    response = client.get("/api/profiles")
    assert response.status_code == 200

    profile = next(p for p in response.json()["profiles"] if p["name"] == "redacted")
    assert profile["api_key_set"] is False


# ── Malformed Bodies ───────────────────────────────────────────────────────


def test_save_profile_missing_llm_field(client):
    """Save without the required 'llm' field returns 422."""
    response = client.post("/api/profiles/missing", json={})
    assert response.status_code == 422


def test_save_profile_wrong_type_for_llm(client):
    """Save with 'llm' as a non-dict returns 422."""
    response = client.post(
        "/api/profiles/bad-llm",
        json={"llm": "not-an-object"},
    )
    assert response.status_code == 422


def test_rename_profile_missing_new_name(client, store):
    """Rename without the required 'new_name' field returns 422."""
    store.save("source", LLM(model="gpt-4o"))
    response = client.post("/api/profiles/source/rename", json={})
    assert response.status_code == 422


def test_rename_profile_empty_new_name(client, store):
    """Rename with empty 'new_name' returns 422."""
    store.save("source", LLM(model="gpt-4o"))
    response = client.post("/api/profiles/source/rename", json={"new_name": ""})
    assert response.status_code == 422


def test_get_profile_corrupted_returns_400(client, temp_profiles_dir):
    """A corrupted profile JSON returns 400 from the load endpoint."""
    (temp_profiles_dir / "broken.json").write_text("{ not valid json")
    response = client.get("/api/profiles/broken")
    assert response.status_code == 400
    assert "broken" in response.json()["detail"].lower()


def test_save_profile_timeout_returns_503(client, monkeypatch):
    """Save endpoint surfaces TimeoutError as 503."""

    def boom(self, name, llm, include_secrets=False, *, max_profiles=None):
        raise TimeoutError("locked")

    monkeypatch.setattr(LLMProfileStore, "save", boom)

    response = client.post(
        "/api/profiles/anything",
        json={"llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 503


def test_rename_profile_timeout_returns_503(client, store, monkeypatch):
    """Rename endpoint surfaces TimeoutError as 503."""
    store.save("src", LLM(model="gpt-4o"))

    def boom(self, old, new):
        raise TimeoutError("locked")

    monkeypatch.setattr(LLMProfileStore, "rename", boom)

    response = client.post("/api/profiles/src/rename", json={"new_name": "dst"})
    assert response.status_code == 503


def test_delete_profile_timeout_returns_503(client, store, monkeypatch):
    """Delete endpoint surfaces TimeoutError as 503."""
    store.save("present", LLM(model="gpt-4o"))

    def boom(self, name):
        raise TimeoutError("locked")

    monkeypatch.setattr(LLMProfileStore, "delete", boom)

    response = client.delete("/api/profiles/present")
    assert response.status_code == 503


def test_whitespace_api_key_reports_not_set(client, store):
    """A profile with a whitespace-only api_key reports api_key_set=False."""
    # Save with a real key, then poke whitespace into the on-disk file.
    store.save("ws", LLM(model="gpt-4o", api_key="placeholder"), include_secrets=True)
    profile_path = store.base_dir / "ws.json"
    profile_path.write_text('{"model": "gpt-4o", "api_key": "   "}')

    response = client.get("/api/profiles")
    profile = next(p for p in response.json()["profiles"] if p["name"] == "ws")
    assert profile["api_key_set"] is False

    detail = client.get("/api/profiles/ws").json()
    assert detail["api_key_set"] is False


def test_save_at_limit_does_not_write_partial_state(client, store, monkeypatch):
    """When the limit is hit, no profile file (or .tmp leftover) should appear."""
    monkeypatch.setattr(profiles_router_module, "MAX_PROFILES", 1)

    store.save("first", LLM(model="gpt-4o"))
    files_before = sorted(p.name for p in store.base_dir.iterdir())

    response = client.post(
        "/api/profiles/second",
        json={"llm": {"model": "gpt-4o"}},
    )
    assert response.status_code == 409

    files_after = sorted(p.name for p in store.base_dir.iterdir())
    assert files_after == files_before  # no new file, no .tmp leftover


def test_get_profile_does_not_expose_api_key(client, store):
    """Even when api_key is saved, GET response nulls it out."""
    llm = LLM(model="gpt-4o", api_key="sk-very-secret")
    store.save("secret-profile", llm, include_secrets=True)

    response = client.get("/api/profiles/secret-profile")
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["api_key"] is None
    assert body["api_key_set"] is True
    # And the secret string itself never appears in the response
    assert "sk-very-secret" not in response.text
