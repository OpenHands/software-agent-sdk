"""Tests for profiles_router endpoints."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server import profiles_router as profiles_router_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores
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
def temp_settings_dir():
    """Create a temporary directory for settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_dir = Path(tmpdir) / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        yield settings_dir


@pytest.fixture
def client(temp_profiles_dir, temp_settings_dir, monkeypatch):
    """Create test client with isolated profiles and settings directories and NO cipher."""
    # Reset store singletons to ensure clean state
    reset_stores()

    # Set environment variable for persistence directory
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_settings_dir))

    # Explicitly disable cipher by setting secret_key to None
    config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
    app = create_app(config)

    # Patch LLMProfileStore to use temp directory
    with patch(
        "openhands.agent_server.profiles_router.LLMProfileStore",
        lambda: LLMProfileStore(base_dir=temp_profiles_dir),
    ):
        yield TestClient(app)

    # Reset stores after test
    reset_stores()


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

    def boom(self, name, *, cipher=None):
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

    def boom(self, name, llm, include_secrets=False, *, cipher=None, max_profiles=None):
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


# ── Cipher Encryption Tests ────────────────────────────────────────────────


@pytest.fixture
def secret_key():
    """Generate a secret key for cipher encryption."""
    from base64 import urlsafe_b64encode

    return urlsafe_b64encode(b"a" * 32).decode("ascii")


@pytest.fixture
def client_with_cipher(temp_profiles_dir, temp_settings_dir, secret_key, monkeypatch):
    """Create test client with cipher configured."""
    from pydantic import SecretStr

    # Reset store singletons to ensure clean state
    reset_stores()

    # Set environment variable for persistence directory
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_settings_dir))

    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(secret_key),
    )
    app = create_app(config)

    with patch(
        "openhands.agent_server.profiles_router.LLMProfileStore",
        lambda: LLMProfileStore(base_dir=temp_profiles_dir),
    ):
        yield TestClient(app)

    # Reset stores after test
    reset_stores()


@pytest.fixture
def cipher(secret_key):
    """Create a cipher instance for testing."""
    from openhands.sdk.utils.cipher import Cipher

    return Cipher(secret_key)


def test_get_profile_invalid_expose_secrets_header_returns_400(client_with_cipher):
    """GET with invalid X-Expose-Secrets header returns 400."""
    response = client_with_cipher.get(
        "/api/profiles/any", headers={"X-Expose-Secrets": "invalid-value"}
    )
    assert response.status_code == 400
    assert "Invalid X-Expose-Secrets" in response.json()["detail"]


def test_get_profile_with_plaintext_header_exposes_secrets(
    client_with_cipher, store, cipher
):
    """GET with X-Expose-Secrets: plaintext returns raw secrets."""
    llm = LLM(model="gpt-4o", api_key="sk-test-secret-key")
    store.save("with-secret", llm, include_secrets=True, cipher=cipher)

    response = client_with_cipher.get(
        "/api/profiles/with-secret", headers={"X-Expose-Secrets": "plaintext"}
    )

    assert response.status_code == 200
    body = response.json()
    # Secret should be exposed
    assert body["config"]["api_key"] == "sk-test-secret-key"


def test_get_profile_with_encrypted_header_encrypts_secrets(
    client_with_cipher, store, cipher
):
    """GET with X-Expose-Secrets: encrypted returns cipher-encrypted secrets."""
    llm = LLM(model="gpt-4o", api_key="sk-test-secret-key")
    store.save("with-secret", llm, include_secrets=True, cipher=cipher)

    response = client_with_cipher.get(
        "/api/profiles/with-secret", headers={"X-Expose-Secrets": "encrypted"}
    )

    assert response.status_code == 200
    body = response.json()
    api_key = body["config"]["api_key"]
    # Should be encrypted (not plaintext, not None)
    assert api_key != "sk-test-secret-key"
    assert api_key is not None
    # Should be decryptable
    decrypted = cipher.decrypt(api_key)
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-test-secret-key"


def test_get_profile_with_true_header_treats_as_encrypted(
    client_with_cipher, store, cipher
):
    """GET with X-Expose-Secrets: true treats as encrypted (safety)."""
    llm = LLM(model="gpt-4o", api_key="sk-test-secret-key")
    store.save("with-secret", llm, include_secrets=True, cipher=cipher)

    response = client_with_cipher.get(
        "/api/profiles/with-secret", headers={"X-Expose-Secrets": "true"}
    )

    assert response.status_code == 200
    body = response.json()
    api_key = body["config"]["api_key"]
    # Should be encrypted (not plaintext)
    assert api_key != "sk-test-secret-key"
    # Should be decryptable
    decrypted = cipher.decrypt(api_key)
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-test-secret-key"


def test_save_profile_with_cipher_encrypts_at_rest(
    client_with_cipher, temp_profiles_dir, cipher
):
    """POST with cipher configured encrypts secrets at rest."""
    import json

    response = client_with_cipher.post(
        "/api/profiles/encrypted-profile",
        json={
            "llm": {"model": "gpt-4o", "api_key": "sk-test-secret"},
            "include_secrets": True,
        },
    )

    assert response.status_code == 201

    # Read raw file to verify encryption
    profile_path = temp_profiles_dir / "encrypted-profile.json"
    data = json.loads(profile_path.read_text())
    # api_key should be encrypted, not plaintext
    assert data["api_key"] != "sk-test-secret"
    # Should be decryptable
    decrypted = cipher.decrypt(data["api_key"])
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-test-secret"


def test_encrypted_roundtrip_workflow(client_with_cipher, store, cipher):
    """Client can GET encrypted, modify, and re-submit encrypted secrets."""
    llm = LLM(model="gpt-4o", api_key="sk-original-secret")
    store.save("roundtrip", llm, include_secrets=True, cipher=cipher)

    get_response = client_with_cipher.get(
        "/api/profiles/roundtrip", headers={"X-Expose-Secrets": "encrypted"}
    )
    assert get_response.status_code == 200
    encrypted_api_key = get_response.json()["config"]["api_key"]

    update_response = client_with_cipher.post(
        "/api/profiles/roundtrip",
        json={
            "llm": {"model": "gpt-4o-mini", "api_key": encrypted_api_key},
            "include_secrets": True,
        },
    )
    assert update_response.status_code == 201

    get_final = client_with_cipher.get(
        "/api/profiles/roundtrip", headers={"X-Expose-Secrets": "plaintext"}
    )
    assert get_final.status_code == 200
    body = get_final.json()
    assert body["config"]["api_key"] == "sk-original-secret"
    assert body["config"]["model"] == "gpt-4o-mini"


def test_save_plaintext_secret_with_cipher_encrypts_at_rest(
    client_with_cipher, temp_profiles_dir, cipher
):
    """First-save path: plaintext input + cipher configured → encrypted on disk."""
    import json

    response = client_with_cipher.post(
        "/api/profiles/first-save",
        json={
            "llm": {"model": "gpt-4o", "api_key": "sk-plaintext-input"},
            "include_secrets": True,
        },
    )
    assert response.status_code == 201

    profile_path = temp_profiles_dir / "first-save.json"
    data = json.loads(profile_path.read_text())
    assert data["api_key"] != "sk-plaintext-input"
    decrypted = cipher.decrypt(data["api_key"])
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-plaintext-input"


def test_get_profile_encrypted_without_cipher_returns_503(client, store):
    """GET with X-Expose-Secrets: encrypted without cipher configured returns 503."""
    llm = LLM(model="gpt-4o", api_key="sk-test-secret")
    store.save("no-cipher", llm, include_secrets=True)

    response = client.get(
        "/api/profiles/no-cipher", headers={"X-Expose-Secrets": "encrypted"}
    )

    assert response.status_code == 503
    body = response.json()
    # 503 errors use "exception" field to avoid leaking internal details
    error_text = body.get("detail", "") + body.get("exception", "")
    assert "OH_SECRET_KEY" in error_text


def test_save_without_cipher_stores_plaintext_for_backward_compat(client, store):
    """POST without cipher configured stores plaintext (backward compatible)."""
    import json

    response = client.post(
        "/api/profiles/plaintext-profile",
        json={
            "llm": {"model": "gpt-4o", "api_key": "sk-plain-secret"},
            "include_secrets": True,
        },
    )

    assert response.status_code == 201

    # Read raw file - should be plaintext
    profile_path = store.base_dir / "plaintext-profile.json"
    data = json.loads(profile_path.read_text())
    assert data["api_key"] == "sk-plain-secret"


# ── Active Profile Tests ───────────────────────────────────────────────────


def test_list_profiles_includes_active_profile_null_by_default(client):
    """GET /api/profiles returns active_profile as null when none is active."""
    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    assert "active_profile" in body
    assert body["active_profile"] is None


def test_activate_profile_success(client, store):
    """POST /api/profiles/{name}/activate activates a profile."""
    llm = LLM(model="gpt-4o", api_key="sk-test-key")
    store.save("my-profile", llm, include_secrets=True)

    response = client.post("/api/profiles/my-profile/activate")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "my-profile"
    assert "activated" in body["message"].lower()
    assert body["llm_applied"] is True


def test_activate_profile_updates_active_profile(client, store):
    """POST /api/profiles/{name}/activate updates the active_profile field."""
    llm = LLM(model="gpt-4o")
    store.save("first-profile", llm)
    store.save("second-profile", llm)

    # Activate first profile
    client.post("/api/profiles/first-profile/activate")
    list_response = client.get("/api/profiles")
    assert list_response.json()["active_profile"] == "first-profile"

    # Activate second profile
    client.post("/api/profiles/second-profile/activate")
    list_response = client.get("/api/profiles")
    assert list_response.json()["active_profile"] == "second-profile"


def test_activate_profile_applies_llm_config(client, store):
    """POST /api/profiles/{name}/activate applies the profile's LLM config."""
    llm = LLM(model="claude-3-opus", temperature=0.8)
    store.save("claude-profile", llm)

    client.post("/api/profiles/claude-profile/activate")

    # Verify the settings were updated
    settings_response = client.get("/api/settings")
    assert settings_response.status_code == 200
    agent_settings = settings_response.json()["agent_settings"]
    assert agent_settings["llm"]["model"] == "claude-3-opus"
    assert agent_settings["llm"]["temperature"] == 0.8


def test_activate_profile_not_found(client):
    """POST /api/profiles/{name}/activate returns 404 for non-existent profile."""
    response = client.post("/api/profiles/nonexistent/activate")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_activate_profile_with_api_key(client, store):
    """POST /api/profiles/{name}/activate applies profile with api_key."""
    llm = LLM(model="gpt-4o", api_key="sk-profile-secret")
    store.save("with-key", llm, include_secrets=True)

    client.post("/api/profiles/with-key/activate")

    # Verify the API key was applied (check llm_api_key_is_set)
    settings_response = client.get("/api/settings")
    assert settings_response.status_code == 200
    assert settings_response.json()["llm_api_key_is_set"] is True


def test_list_profiles_shows_active_after_activation(client, store):
    """GET /api/profiles shows the correct active_profile after activation."""
    llm = LLM(model="gpt-4o")
    store.save("profile-a", llm)
    store.save("profile-b", llm)

    # Initially no active profile
    response = client.get("/api/profiles")
    assert response.json()["active_profile"] is None

    # Activate profile-a
    client.post("/api/profiles/profile-a/activate")
    response = client.get("/api/profiles")
    body = response.json()
    assert body["active_profile"] == "profile-a"

    # Verify profile-a is in the list
    names = {p["name"] for p in body["profiles"]}
    assert "profile-a" in names
    assert "profile-b" in names


def test_activate_profile_invalid_name(client):
    """POST /api/profiles/{name}/activate rejects invalid profile names."""
    # Path traversal attempt
    response = client.post("/api/profiles/..%2Fetc%2Fpasswd/activate")
    assert response.status_code in (404, 422)

    # Hidden file attempt
    response = client.post("/api/profiles/.hidden/activate")
    assert response.status_code in (400, 404, 422)


# ── Rename Active Profile Tests ───────────────────────────────────────────


def test_rename_active_profile_updates_active_profile(client, store):
    """Renaming the active profile should update active_profile in settings."""
    # Create and activate a profile
    llm = LLM(model="gpt-4o", api_key=SecretStr("sk-test"))
    store.save("my-profile", llm)
    client.post("/api/profiles/my-profile/activate")

    # Verify it's active
    response = client.get("/api/profiles")
    assert response.json()["active_profile"] == "my-profile"

    # Rename the active profile
    response = client.post(
        "/api/profiles/my-profile/rename",
        json={"new_name": "renamed-profile"},
    )
    assert response.status_code == 200

    # Verify active_profile was updated to the new name
    response = client.get("/api/profiles")
    assert response.status_code == 200
    body = response.json()
    assert body["active_profile"] == "renamed-profile"
    assert len(body["profiles"]) == 1
    assert body["profiles"][0]["name"] == "renamed-profile"


def test_rename_inactive_profile_preserves_active_profile(client, store):
    """Renaming a non-active profile should not change active_profile."""
    # Create two profiles
    llm1 = LLM(model="gpt-4o", api_key=SecretStr("sk-test1"))
    llm2 = LLM(model="claude-3-opus", api_key=SecretStr("sk-test2"))
    store.save("profile-a", llm1)
    store.save("profile-b", llm2)

    # Activate profile-a
    client.post("/api/profiles/profile-a/activate")

    # Rename profile-b (not the active one)
    response = client.post(
        "/api/profiles/profile-b/rename",
        json={"new_name": "profile-b-renamed"},
    )
    assert response.status_code == 200

    # Verify active_profile is still profile-a
    response = client.get("/api/profiles")
    assert response.json()["active_profile"] == "profile-a"


# ── Auto-Create Profile Tests ─────────────────────────────────────────────


def test_list_profiles_auto_creates_profile_named_after_model(client):
    """GET /api/profiles auto-creates profile named after model when API key is configured."""
    # Configure LLM settings with API key (required for auto-creation)
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {"model": "gpt-4o", "api_key": "sk-auto-test", "temperature": 0.5}
            }
        },
    )

    # List profiles should auto-create a profile named after the model
    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    assert len(body["profiles"]) == 1
    assert body["profiles"][0]["name"] == "gpt-4o"  # Named after model
    assert body["profiles"][0]["model"] == "gpt-4o"
    assert body["profiles"][0]["api_key_set"] is True
    assert body["active_profile"] == "gpt-4o"


def test_list_profiles_auto_creates_profile_strips_provider_prefix(client):
    """Auto-created profile strips provider prefix from model name."""
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {"model": "openai/gpt-4o-mini", "api_key": "sk-prefix-test"}
            }
        },
    )

    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    # Should use just "gpt-4o-mini" not "openai/gpt-4o-mini"
    assert body["profiles"][0]["name"] == "gpt-4o-mini"
    assert body["active_profile"] == "gpt-4o-mini"


def test_list_profiles_auto_creates_profile_sanitizes_special_chars(client):
    """Auto-created profile sanitizes special characters in model name."""
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {"model": "anthropic/claude-3.5-sonnet@beta", "api_key": "sk-special"}
            }
        },
    )

    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    # @ should be replaced with -
    assert body["profiles"][0]["name"] == "claude-3.5-sonnet-beta"


def test_list_profiles_no_auto_create_without_api_key(client):
    """No auto-creation when agent_settings.llm has no API key."""
    # Configure model but no API key
    client.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )

    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    assert body["profiles"] == []
    assert body["active_profile"] is None


def test_list_profiles_no_auto_create_when_no_config(client):
    """No auto-creation when using default settings (no explicit configuration)."""
    # Don't configure anything - leave settings empty
    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    assert body["profiles"] == []
    assert body["active_profile"] is None


def test_list_profiles_no_auto_create_when_profiles_exist(client, store):
    """No auto-creation when profiles already exist."""
    # Create a profile first
    llm = LLM(model="claude-3-opus")
    store.save("existing-profile", llm)

    # Configure different LLM in settings with API key
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {"model": "gpt-4o", "api_key": "sk-should-not-auto"}
            }
        },
    )

    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    # Only the existing profile, no auto-created one
    assert len(body["profiles"]) == 1
    assert body["profiles"][0]["name"] == "existing-profile"


def test_list_profiles_auto_create_is_idempotent(client):
    """Multiple calls to list_profiles don't create duplicate profiles."""
    # Configure LLM with API key
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {"model": "gpt-4o", "api_key": "sk-idempotent-test"}
            }
        },
    )

    # First call creates profile
    response1 = client.get("/api/profiles")
    assert response1.status_code == 200
    assert len(response1.json()["profiles"]) == 1

    # Second call should not create another
    response2 = client.get("/api/profiles")
    assert response2.status_code == 200
    assert len(response2.json()["profiles"]) == 1
    assert response2.json()["profiles"][0]["name"] == "gpt-4o"


def test_auto_created_profile_persists(client, store):
    """Auto-created profile is persisted and can be loaded."""
    # Configure LLM with API key
    client.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {"model": "gpt-4o", "api_key": "sk-persist-test", "temperature": 0.7}
            }
        },
    )

    # Trigger auto-creation
    client.get("/api/profiles")

    # Verify profile was saved with model name
    loaded = store.load("gpt-4o")
    assert loaded.model == "gpt-4o"
    assert loaded.temperature == 0.7
    assert loaded.api_key is not None
    assert loaded.api_key.get_secret_value() == "sk-persist-test"
