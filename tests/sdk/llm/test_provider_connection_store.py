"""Tests for ProviderConnectionStore and its resolution in LLMProfileStore."""

import json
import time

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.provider_connection_store import (
    PROVIDER_CONNECTIONS_SCHEMA_VERSION,
    ProviderConnection,
    ProviderConnectionNotFound,
    ProviderConnectionStore,
)
from openhands.sdk.utils.cipher import Cipher


def _connection(**overrides) -> ProviderConnection:
    now = int(time.time())
    data = {
        "id": "conn1",
        "display_name": "Anthropic",
        "provider": "anthropic",
        "api_key": "sk-shared",
        "base_url": "https://api.anthropic.com",
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ProviderConnection(**data)


def test_provider_connection_not_found_is_value_error():
    """The agent-server relies on this subclassing so generic ``ValueError``
    handlers (OpenAI gateway, profile resolver) degrade a dangling reference to
    a 4xx instead of an opaque 500."""
    assert issubclass(ProviderConnectionNotFound, ValueError)


def test_crud_roundtrip(tmp_path):
    store = ProviderConnectionStore(base_dir=tmp_path)
    assert store.list() == []

    store.create(_connection())
    got = store.get("conn1")
    assert got is not None
    assert got.api_key_value() == "sk-shared"

    store.update(_connection(display_name="Renamed", api_key="sk-new"))
    renamed = store.get("conn1")
    assert renamed is not None
    assert renamed.display_name == "Renamed"
    assert renamed.api_key_value() == "sk-new"

    store.delete("conn1")
    assert store.get("conn1") is None
    with pytest.raises(ProviderConnectionNotFound):
        store.delete("conn1")


def test_api_key_encrypted_at_rest(tmp_path):
    cipher = Cipher("unit-test-secret-key")
    store = ProviderConnectionStore(base_dir=tmp_path)
    store.create(_connection(), cipher=cipher)

    raw = json.loads((tmp_path / "provider_connections.json").read_text())
    stored_key = raw["connections"][0]["api_key"]
    assert stored_key != "sk-shared"  # not plaintext
    assert raw["schema_version"] == PROVIDER_CONNECTIONS_SCHEMA_VERSION

    # Round-trips back to plaintext with the same cipher.
    loaded = store.get("conn1", cipher=cipher)
    assert loaded is not None
    assert loaded.api_key_value() == "sk-shared"


def test_corrupted_file_raises_not_clobbers(tmp_path):
    path = tmp_path / "provider_connections.json"
    path.write_text("{ not valid json")
    store = ProviderConnectionStore(base_dir=tmp_path)
    with pytest.raises(ValueError):
        store.list()
    # The corrupt file is left intact, not silently replaced.
    assert path.read_text() == "{ not valid json"


# ── Resolution in LLMProfileStore ──────────────────────────────────────────


def test_load_without_provider_is_unchanged(tmp_path):
    """Rule 1/4: no provider reference -> byte-identical old behavior."""
    store = LLMProfileStore(base_dir=tmp_path / "profiles")
    store.save("p", LLM(model="gpt-4o", api_key="sk-own"), include_secrets=True)
    llm = store.load("p")
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == "sk-own"


def test_save_clears_inline_credentials_when_linked(tmp_path):
    """Rule 5c: a linked profile persists no inline api_key / base_url."""
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    profiles = LLMProfileStore(base_dir=tmp_path / "profiles", provider_store=provider)
    profiles.save(
        "p",
        LLM(
            model="anthropic/claude-sonnet-4",
            api_key="sk-should-be-dropped",
            base_url="https://should.drop",
            provider_connection_id="conn1",
        ),
        include_secrets=True,
    )
    raw = json.loads((tmp_path / "profiles" / "p.json").read_text())
    assert raw.get("api_key") in (None, "")
    assert raw.get("base_url") is None
    assert raw["provider_connection_id"] == "conn1"


def test_load_resolves_provider_credentials(tmp_path):
    """Rule 5: connection api_key / base_url are applied at load (read-at-use)."""
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    provider.create(_connection())
    profiles = LLMProfileStore(base_dir=tmp_path / "profiles", provider_store=provider)
    profiles.save(
        "p",
        LLM(model="anthropic/claude-sonnet-4", provider_connection_id="conn1"),
    )

    llm = profiles.load("p")
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == "sk-shared"
    assert llm.base_url == "https://api.anthropic.com"

    # Rotation takes effect on the next load, nothing cached.
    provider.update(_connection(api_key="sk-rotated"))
    rotated = profiles.load("p")
    assert isinstance(rotated.api_key, SecretStr)
    assert rotated.api_key.get_secret_value() == "sk-rotated"


def test_load_base_url_authoritative_including_none(tmp_path):
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    provider.create(_connection(base_url=None))
    profiles = LLMProfileStore(base_dir=tmp_path / "profiles", provider_store=provider)
    profiles.save(
        "p",
        LLM(
            model="openai/gpt-5.5",
            base_url="https://profile.example",
            provider_connection_id="conn1",
        ),
    )
    assert profiles.load("p").base_url is None


def test_load_missing_connection_raises(tmp_path):
    """Rule 5b: dangling reference with no inline key fails loudly."""
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    profiles = LLMProfileStore(base_dir=tmp_path / "profiles", provider_store=provider)
    profiles.save(
        "p",
        LLM(model="anthropic/claude-sonnet-4", provider_connection_id="ghost"),
    )
    with pytest.raises(ProviderConnectionNotFound):
        profiles.load("p")

    # resolve_provider=False inspects the stored profile without resolving.
    llm = profiles.load("p", resolve_provider=False)
    assert llm.provider_connection_id == "ghost"


def test_list_summaries_reports_linked_key_presence(tmp_path):
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    provider.create(_connection())
    profiles = LLMProfileStore(base_dir=tmp_path / "profiles", provider_store=provider)
    profiles.save(
        "p",
        LLM(model="anthropic/claude-sonnet-4", provider_connection_id="conn1"),
    )
    summary = profiles.list_summaries()[0]
    assert summary["provider_connection_id"] == "conn1"
    assert summary["api_key_set"] is True
    assert summary["provider_connection_broken"] is False


def test_list_summaries_marks_broken_when_connection_deleted(tmp_path):
    """Fix (enyst): deleting a provider connection must surface as
    ``provider_connection_broken=True`` on every profile that referenced it,
    so the UI can show a 'Broken link' indicator rather than silently failing
    on activation."""
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    provider.create(_connection())
    profiles = LLMProfileStore(base_dir=tmp_path / "profiles", provider_store=provider)
    profiles.save("p", LLM(model="anthropic/claude-sonnet-4", provider_connection_id="conn1"))

    # Confirm not broken before deletion.
    assert profiles.list_summaries()[0]["provider_connection_broken"] is False

    # Delete the connection; the profile on disk still references it.
    provider.delete("conn1")

    summary = profiles.list_summaries()[0]
    assert summary["provider_connection_broken"] is True
    assert summary["provider_connection_id"] == "conn1"
    # api_key_set must also be False (no inline key, no connection).
    assert summary["api_key_set"] is False


def test_bare_profile_store_auto_wires_provider_store(tmp_path, monkeypatch):
    """Fix: bare LLMProfileStore() auto-creates a ProviderConnectionStore so that
    standalone SDK paths (LocalConversation, FallbackStrategy, switch_llm) can
    resolve linked profiles saved by the agent-server."""
    import openhands.sdk.llm.provider_connection_store as pcs_mod

    # Redirect the default provider-connection dir so the test is isolated.
    monkeypatch.setattr(pcs_mod, "_DEFAULT_DIR", tmp_path / "conns")
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    provider.create(_connection())

    # Construct with only base_dir — no explicit provider_store arg.
    profiles = LLMProfileStore(base_dir=tmp_path / "profiles")
    profiles.save("p", LLM(model="gpt-4o", provider_connection_id="conn1"))

    llm = profiles.load("p")
    assert llm.api_key is not None
    assert llm.api_key.get_secret_value() == "sk-shared"
    assert llm.base_url == "https://api.anthropic.com"


def test_wrong_cipher_update_raises_not_destroys_key(tmp_path):
    """Fix: a read-modify-write with a wrong cipher must raise a ValidationError,
    not silently persist api_key=null and destroy the stored ciphertext."""
    from pydantic import ValidationError

    cipher_a = Cipher("key-a")
    cipher_b = Cipher("key-b")
    store = ProviderConnectionStore(base_dir=tmp_path)
    store.create(_connection(), cipher=cipher_a)

    # Attempting to update with the wrong cipher must fail before any write.
    with pytest.raises((ValidationError, ValueError)):
        store.update(_connection(display_name="updated"), cipher=cipher_b)

    # The stored key must still be intact — readable with the original cipher.
    restored = store.get("conn1", cipher=cipher_a)
    assert restored is not None
    assert restored.api_key_value() == "sk-shared"
