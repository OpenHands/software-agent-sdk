"""Tests for LLM router."""

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.llm_router import (
    list_models,
    list_providers,
    list_verified_models,
)
from openhands.sdk.llm.auth.openai import OPENAI_CODEX_MODELS
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS


@pytest.fixture
def client():
    """Create a test client."""
    config = Config(session_api_keys=[])  # Disable authentication for tests
    app = create_app(config)
    return TestClient(app)


@pytest.mark.asyncio
async def test_list_providers():
    """Test listing providers directly."""
    response = await list_providers()
    assert len(response.providers) > 0
    assert "openai" in response.providers
    assert "anthropic" in response.providers
    assert response.providers == sorted(response.providers)


@pytest.mark.asyncio
async def test_list_models():
    """Test listing models directly."""
    response = await list_models(provider=None)
    assert len(response.models) > 0
    assert response.models == sorted(set(response.models))


@pytest.mark.asyncio
async def test_list_models_filtered_by_provider():
    """Test listing models filtered by provider."""
    response = await list_models(provider="openai")
    assert len(response.models) > 0
    # Verify filtering works - there should be fewer models than unfiltered
    all_models_response = await list_models(provider=None)
    assert len(response.models) < len(all_models_response.models)


@pytest.mark.asyncio
async def test_list_models_unknown_provider():
    """Test listing models with an unknown provider returns empty list."""
    response = await list_models(provider="unknown_provider_xyz")
    assert response.models == []


@pytest.mark.asyncio
async def test_list_verified_models():
    """Test listing verified models directly."""
    response = await list_verified_models()
    assert response.models == VERIFIED_MODELS
    assert "openai" in response.models
    assert "anthropic" in response.models


def test_providers_endpoint_integration(client):
    """Test providers endpoint through the API."""
    response = client.get("/api/llm/providers")
    assert response.status_code == 200
    data = response.json()
    assert "providers" in data
    assert len(data["providers"]) > 0
    assert "openai" in data["providers"]


def test_models_endpoint_integration(client):
    """Test models endpoint through the API."""
    response = client.get("/api/llm/models")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert len(data["models"]) > 0


def test_models_endpoint_with_provider_filter(client):
    """Test models endpoint with provider query parameter."""
    response = client.get("/api/llm/models?provider=openai")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert len(data["models"]) > 0


def test_models_endpoint_with_unknown_provider(client):
    """Test models endpoint with unknown provider returns empty list."""
    response = client.get("/api/llm/models?provider=unknown_provider_xyz")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert data["models"] == []


def test_verified_models_endpoint_integration(client):
    """Test verified models endpoint through the API."""
    response = client.get("/api/llm/models/verified")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert "openai" in data["models"]
    assert "anthropic" in data["models"]


def test_openai_subscription_models_endpoint_integration(client):
    """Test subscription models endpoint through the API."""
    response = client.get("/api/llm/subscription/openai/models")
    assert response.status_code == 200
    data = response.json()
    assert data == {"vendor": "openai", "models": sorted(OPENAI_CODEX_MODELS)}
    assert "gpt-5.5" in data["models"]


def test_openai_subscription_status_endpoint_does_not_return_tokens(
    client, monkeypatch
):
    """Status reports safe metadata without exposing OAuth tokens."""
    from openhands.agent_server import llm_router
    from openhands.sdk.llm.auth.credentials import OAuthCredentials

    class FakeAuth:
        async def refresh_if_needed(self):
            return OAuthCredentials(
                vendor="openai",
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=4_102_444_800_000,
            )

        def get_credentials(self):
            return OAuthCredentials(
                vendor="openai",
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=4_102_444_800_000,
            )

    monkeypatch.setattr(llm_router, "_get_openai_subscription_auth", FakeAuth)

    response = client.get("/api/llm/subscription/openai/status")

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "vendor": "openai",
        "connected": True,
        "account_email": None,
        "expires_at": 4_102_444_800_000,
    }
    assert "access_token" not in response.text
    assert "refresh_token" not in response.text


def test_openai_subscription_device_start_returns_opaque_poll_token(
    client, monkeypatch
):
    """Device start stores OpenAI internals server-side."""
    from openhands.agent_server import llm_router
    from openhands.sdk.llm.auth.openai import DeviceCode

    class FakeAuth:
        async def start_device_login(self):
            return DeviceCode(
                verification_url="https://auth.example/device",
                user_code="ABCD-EFGH",
                device_auth_id="openai-device-auth-id",
                interval=7,
            )

    monkeypatch.setattr(llm_router, "_get_openai_subscription_auth", FakeAuth)
    monkeypatch.setattr(llm_router.secrets, "token_urlsafe", lambda _: "opaque-token")

    response = client.post("/api/llm/subscription/openai/device/start")

    assert response.status_code == 200
    data = response.json()
    assert data["device_code"] == "opaque-token"
    assert data["user_code"] == "ABCD-EFGH"
    assert data["verification_uri"] == "https://auth.example/device"
    assert data["interval_seconds"] == 7
    assert "openai-device-auth-id" not in response.text


def test_openai_subscription_device_start_rate_limit_returns_429(client, monkeypatch):
    """OpenAI device-code rate limits are reported as retryable client errors."""
    from openhands.agent_server import llm_router
    from openhands.sdk.llm.auth.openai import OpenAIDeviceAuthError

    class FakeAuth:
        async def start_device_login(self):
            raise OpenAIDeviceAuthError(
                "Device code request failed with status 429",
                status_code=429,
            )

    monkeypatch.setattr(llm_router, "_get_openai_subscription_auth", FakeAuth)

    response = client.post("/api/llm/subscription/openai/device/start")

    assert response.status_code == 429
    assert response.json() == {
        "detail": (
            "OpenAI is rate-limiting ChatGPT device-code sign-in. "
            "Wait a few minutes before trying again, or configure an OpenAI API key."
        )
    }


def test_openai_subscription_device_poll_pending_and_success(client, monkeypatch):
    """Polling returns disconnected while pending and connected after success."""
    from openhands.agent_server import llm_router
    from openhands.sdk.llm.auth.credentials import OAuthCredentials
    from openhands.sdk.llm.auth.openai import DeviceCode

    llm_router._PENDING_OPENAI_DEVICE_LOGINS.clear()
    llm_router._PENDING_OPENAI_DEVICE_LOGINS["opaque-token"] = (
        llm_router.PendingDeviceLogin(
            device_code=DeviceCode(
                verification_url="https://auth.example/device",
                user_code="ABCD-EFGH",
                device_auth_id="openai-device-auth-id",
                interval=1,
            ),
            expires_at=int(llm_router.time.time() * 1000) + 60_000,
            epoch=llm_router._OPENAI_DEVICE_LOGIN_EPOCH,
        )
    )

    class FakeAuth:
        calls = 0

        saved_credentials = None

        async def poll_device_login(self, device_code, *, persist=True):
            assert persist is False
            self.__class__.calls += 1
            if self.__class__.calls == 1:
                return None
            return OAuthCredentials(
                vendor="openai",
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=4_102_444_800_000,
            )

        def save_credentials(self, credentials):
            self.__class__.saved_credentials = credentials

    monkeypatch.setattr(llm_router, "_get_openai_subscription_auth", FakeAuth)

    pending = client.post(
        "/api/llm/subscription/openai/device/poll",
        json={"device_code": "opaque-token"},
    )
    success = client.post(
        "/api/llm/subscription/openai/device/poll",
        json={"device_code": "opaque-token"},
    )

    assert pending.status_code == 200
    assert pending.json()["connected"] is False
    assert success.status_code == 200
    assert success.json()["connected"] is True
    assert success.json()["expires_at"] == 4_102_444_800_000
    assert FakeAuth.saved_credentials is not None
    assert "access-token" not in success.text
    assert "opaque-token" not in llm_router._PENDING_OPENAI_DEVICE_LOGINS


def test_openai_subscription_device_poll_rate_limit_keeps_pending_login(
    client, monkeypatch
):
    """OpenAI device-auth rate limits do not consume the opaque poll token."""
    from openhands.agent_server import llm_router
    from openhands.sdk.llm.auth.credentials import OAuthCredentials
    from openhands.sdk.llm.auth.openai import DeviceCode, OpenAIDeviceAuthError

    llm_router._PENDING_OPENAI_DEVICE_LOGINS.clear()
    llm_router._IN_FLIGHT_OPENAI_DEVICE_LOGINS.clear()
    llm_router._PENDING_OPENAI_DEVICE_LOGINS["opaque-token"] = (
        llm_router.PendingDeviceLogin(
            device_code=DeviceCode(
                verification_url="https://auth.example/device",
                user_code="ABCD-EFGH",
                device_auth_id="openai-device-auth-id",
                interval=1,
            ),
            expires_at=int(llm_router.time.time() * 1000) + 60_000,
            epoch=llm_router._OPENAI_DEVICE_LOGIN_EPOCH,
        )
    )

    class FakeAuth:
        calls = 0

        saved_credentials = None

        async def poll_device_login(self, device_code, *, persist=True):
            assert persist is False
            self.__class__.calls += 1
            if self.__class__.calls == 1:
                raise OpenAIDeviceAuthError(
                    "Device auth failed with status 429",
                    status_code=429,
                )
            return OAuthCredentials(
                vendor="openai",
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=4_102_444_800_000,
            )

        def save_credentials(self, credentials):
            self.__class__.saved_credentials = credentials

    monkeypatch.setattr(llm_router, "_get_openai_subscription_auth", FakeAuth)

    rate_limited = client.post(
        "/api/llm/subscription/openai/device/poll",
        json={"device_code": "opaque-token"},
    )
    success = client.post(
        "/api/llm/subscription/openai/device/poll",
        json={"device_code": "opaque-token"},
    )

    assert rate_limited.status_code == 429
    assert "rate-limiting" in rate_limited.json()["detail"]
    assert success.status_code == 200
    assert success.json()["connected"] is True
    assert FakeAuth.saved_credentials is not None
    assert "access-token" not in success.text
    assert "opaque-token" not in llm_router._PENDING_OPENAI_DEVICE_LOGINS
    assert "opaque-token" not in llm_router._IN_FLIGHT_OPENAI_DEVICE_LOGINS


@pytest.mark.asyncio
async def test_openai_subscription_device_poll_failure_keeps_pending_login(
    monkeypatch,
):
    """Transient provider failures do not consume the opaque poll token."""
    from openhands.agent_server import llm_router
    from openhands.sdk.llm.auth.credentials import OAuthCredentials
    from openhands.sdk.llm.auth.openai import DeviceCode

    llm_router._PENDING_OPENAI_DEVICE_LOGINS.clear()
    llm_router._IN_FLIGHT_OPENAI_DEVICE_LOGINS.clear()
    llm_router._PENDING_OPENAI_DEVICE_LOGINS["opaque-token"] = (
        llm_router.PendingDeviceLogin(
            device_code=DeviceCode(
                verification_url="https://auth.example/device",
                user_code="ABCD-EFGH",
                device_auth_id="openai-device-auth-id",
                interval=1,
            ),
            expires_at=int(llm_router.time.time() * 1000) + 60_000,
            epoch=llm_router._OPENAI_DEVICE_LOGIN_EPOCH,
        )
    )

    class FakeAuth:
        calls = 0
        saved_credentials = None

        async def poll_device_login(self, device_code, *, persist=True):
            assert persist is False
            self.__class__.calls += 1
            if self.__class__.calls == 1:
                raise RuntimeError("temporary provider failure")
            return OAuthCredentials(
                vendor="openai",
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=4_102_444_800_000,
            )

        def save_credentials(self, credentials):
            self.__class__.saved_credentials = credentials

    monkeypatch.setattr(llm_router, "_get_openai_subscription_auth", FakeAuth)

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        await llm_router.poll_openai_subscription_device_login(
            llm_router.SubscriptionDevicePollRequest(device_code="opaque-token")
        )
    assert "opaque-token" in llm_router._PENDING_OPENAI_DEVICE_LOGINS

    success = await llm_router.poll_openai_subscription_device_login(
        llm_router.SubscriptionDevicePollRequest(device_code="opaque-token")
    )

    assert success.connected is True
    assert FakeAuth.saved_credentials is not None
    assert "opaque-token" not in llm_router._PENDING_OPENAI_DEVICE_LOGINS
    assert "opaque-token" not in llm_router._IN_FLIGHT_OPENAI_DEVICE_LOGINS


def test_openai_subscription_logout_endpoint(client, monkeypatch):
    """Logout removes credentials and returns disconnected status."""
    from openhands.agent_server import llm_router

    llm_router._PENDING_OPENAI_DEVICE_LOGINS["opaque-token"] = (
        llm_router.PendingDeviceLogin(
            device_code=llm_router.DeviceCode(
                verification_url="https://auth.example/device",
                user_code="ABCD-EFGH",
                device_auth_id="openai-device-auth-id",
                interval=1,
            ),
            expires_at=int(llm_router.time.time() * 1000) + 60_000,
            epoch=llm_router._OPENAI_DEVICE_LOGIN_EPOCH,
        )
    )

    class FakeAuth:
        logged_out = False

        def logout(self):
            self.__class__.logged_out = True
            return True

    monkeypatch.setattr(llm_router, "_get_openai_subscription_auth", FakeAuth)

    response = client.post("/api/llm/subscription/openai/logout")

    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert FakeAuth.logged_out is True
    assert llm_router._PENDING_OPENAI_DEVICE_LOGINS == {}
