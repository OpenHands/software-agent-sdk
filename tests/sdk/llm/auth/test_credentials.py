"""Tests for credential storage and retrieval."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from openhands.sdk.llm.auth import openai
from openhands.sdk.llm.auth.credentials import (
    CredentialStore,
    OAuthCredentials,
    SubscriptionBrokerCredentialStore,
    get_credentials_dir,
)
from openhands.sdk.llm.auth.openai import (
    OpenAISubscriptionAuth,
    subscription_credential_store_scope,
)
from openhands.sdk.llm.llm import LLM


def test_oauth_credentials_model():
    """Test OAuthCredentials model creation and validation."""
    expires_at = int(time.time() * 1000) + 3600_000  # 1 hour from now
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        expires_at=expires_at,
    )
    assert creds.vendor == "openai"
    assert creds.access_token == "test_access_token"
    assert creds.refresh_token == "test_refresh_token"
    assert creds.expires_at == expires_at
    assert creds.type == "oauth"


def test_oauth_credentials_is_expired():
    """Test OAuthCredentials expiration check."""
    # Not expired (1 hour from now)
    future_creds = OAuthCredentials(
        vendor="openai",
        access_token="test",
        refresh_token="test",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    assert not future_creds.is_expired()

    # Expired (1 hour ago)
    past_creds = OAuthCredentials(
        vendor="openai",
        access_token="test",
        refresh_token="test",
        expires_at=int(time.time() * 1000) - 3600_000,
    )
    assert past_creds.is_expired()


def test_get_credentials_dir_default(monkeypatch):
    """Test default credentials directory."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    creds_dir = get_credentials_dir()
    assert creds_dir == Path.home() / ".openhands" / "auth"


def test_get_credentials_dir_xdg(monkeypatch, tmp_path):
    """Test credentials directory ignores XDG_DATA_HOME (uses ~/.openhands/auth)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    creds_dir = get_credentials_dir()
    # Implementation uses ~/.openhands/auth regardless of XDG_DATA_HOME
    assert creds_dir == Path.home() / ".openhands" / "auth"


def test_credential_store_save_and_get(tmp_path):
    """Test saving and retrieving credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test_access",
        refresh_token="test_refresh",
        expires_at=int(time.time() * 1000) + 3600_000,
    )

    store.save(creds)

    # Verify file was created
    creds_file = tmp_path / "openai_oauth.json"
    assert creds_file.exists()

    # Verify file permissions (owner read/write only)
    if os.name != "nt":
        assert (creds_file.stat().st_mode & 0o777) == 0o600

    # Retrieve and verify
    retrieved = store.get("openai")
    assert retrieved is not None
    assert retrieved.vendor == creds.vendor
    assert retrieved.access_token == creds.access_token
    assert retrieved.refresh_token == creds.refresh_token
    assert retrieved.expires_at == creds.expires_at


def test_credential_store_get_nonexistent(tmp_path):
    """Test getting credentials that don't exist."""
    store = CredentialStore(credentials_dir=tmp_path)
    assert store.get("nonexistent") is None


def test_credential_store_get_invalid_json(tmp_path):
    """Test getting credentials from invalid JSON file."""
    store = CredentialStore(credentials_dir=tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    # Create invalid JSON file
    creds_file = tmp_path / "openai_oauth.json"
    creds_file.write_text("invalid json")

    # Should return None and delete the invalid file
    assert store.get("openai") is None
    assert not creds_file.exists()


def test_credential_store_delete(tmp_path):
    """Test deleting credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    creds = OAuthCredentials(
        vendor="openai",
        access_token="test",
        refresh_token="test",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(creds)

    # Delete and verify
    assert store.delete("openai") is True
    assert store.get("openai") is None

    # Delete again should return False
    assert store.delete("openai") is False


def test_credential_store_update_tokens(tmp_path):
    """Test updating tokens for existing credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    original = OAuthCredentials(
        vendor="openai",
        access_token="old_access",
        refresh_token="old_refresh",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(original)

    # Update tokens
    updated = store.update_tokens(
        vendor="openai",
        access_token="new_access",
        refresh_token="new_refresh",
        expires_in=7200,  # 2 hours
    )

    assert updated is not None
    assert updated.access_token == "new_access"
    assert updated.refresh_token == "new_refresh"

    # Verify persisted
    retrieved = store.get("openai")
    assert retrieved is not None
    assert retrieved.access_token == "new_access"


def test_credential_store_update_tokens_keeps_refresh_if_not_provided(tmp_path):
    """Test that update_tokens keeps old refresh token if new one not provided."""
    store = CredentialStore(credentials_dir=tmp_path)
    original = OAuthCredentials(
        vendor="openai",
        access_token="old_access",
        refresh_token="original_refresh",
        expires_at=int(time.time() * 1000) + 3600_000,
    )
    store.save(original)

    # Update without new refresh token
    updated = store.update_tokens(
        vendor="openai",
        access_token="new_access",
        refresh_token=None,
        expires_in=3600,
    )

    assert updated is not None
    assert updated.access_token == "new_access"
    assert updated.refresh_token == "original_refresh"


def test_credential_store_update_tokens_nonexistent(tmp_path):
    """Test updating tokens for non-existent credentials."""
    store = CredentialStore(credentials_dir=tmp_path)
    result = store.update_tokens(
        vendor="openai",
        access_token="new_access",
        refresh_token="new_refresh",
        expires_in=3600,
    )
    assert result is None


def test_disabled_shared_server_never_reads_default_filesystem(monkeypatch, tmp_path):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    monkeypatch.setenv("OH_OPENAI_SUBSCRIPTION_MODE", "disabled")
    local = CredentialStore(tmp_path / "auth")
    local.save(
        OAuthCredentials(
            vendor="openai",
            access_token="other-user",
            refresh_token="secret",
            expires_at=int(time.time() * 1000) + 3600000,
        )
    )
    with pytest.raises(RuntimeError, match="disabled"):
        OpenAISubscriptionAuth()
    assert (
        OpenAISubscriptionAuth(
            credential_store=CredentialStore(tmp_path / "empty")
        ).get_credentials()
        is None
    )
    with subscription_credential_store_scope(local):
        assert OpenAISubscriptionAuth().get_credentials().access_token == "other-user"
    with pytest.raises(RuntimeError, match="disabled"):
        OpenAISubscriptionAuth()


@pytest.fixture
def broker_server():
    state = {"requests": [], "token": "first-access", "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/jwks":
                body = json.dumps({"keys": []}).encode()
                self.send_response(200)
            else:
                state["requests"].append(self.headers.get("X-Session-API-Key"))
                self.send_response(state["status"])
                body = json.dumps(
                    {
                        "access_token": state["token"],
                        "expires_at": int(time.time() * 1000) + 3600000,
                    }
                ).encode()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_broker_uses_current_initialized_session_key(broker_server, monkeypatch):
    url, state = broker_server
    monkeypatch.setenv("OH_OPENAI_SUBSCRIPTION_MODE", "broker")
    monkeypatch.setenv("OH_OPENAI_SUBSCRIPTION_BROKER_URL", f"{url}/token")
    monkeypatch.setenv("SESSION_API_KEY", "stale-template-key")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "initialized-session-key")
    assert OpenAISubscriptionAuth().get_credentials().access_token == "first-access"
    assert state["requests"] == ["initialized-session-key"]


@pytest.mark.asyncio
async def test_broker_revalidates_each_request_and_logout_fails_closed(
    broker_server, monkeypatch
):
    url, state = broker_server
    monkeypatch.setattr(openai, "JWKS_URL", f"{url}/jwks")
    openai._jwks_cache.clear()
    store = SubscriptionBrokerCredentialStore(f"{url}/token", "session-key")
    llm = LLM(model="openai/gpt-5.5", auth_type="subscription")
    llm.is_subscription = True
    llm._subscription_credential_store = store
    llm._subscription_credentials = OAuthCredentials(
        vendor="openai",
        access_token="cached-secret",
        refresh_token="",
        expires_at=int(time.time() * 1000) + 3600000,
    )
    assert llm._get_litellm_api_key_value() == "first-access"
    state["token"] = "second-access"
    assert await llm._aget_litellm_api_key_value() == "second-access"
    state["status"] = 404
    with pytest.raises(ValueError, match="login is required"):
        await llm._aget_litellm_api_key_value()
    with pytest.raises(ValueError, match="login is required"):
        llm._get_litellm_api_key_value()
    assert state["requests"] == ["session-key"] * 4
    assert "first-access" not in repr(llm.model_dump(context={"expose_secrets": True}))
    openai._jwks_cache.clear()
