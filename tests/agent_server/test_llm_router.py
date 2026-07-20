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
    assert "gpt-5.6" in response.models
    assert "gpt-5.6-sol" in response.models
    assert "gpt-5.6-terra" in response.models
    assert "gpt-5.6-luna" in response.models
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
    assert "gpt-5.6" in data["models"]


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
    assert "gpt-5.6" in data["models"]["openai"]


def test_openai_subscription_models_endpoint_integration(client):
    """Test subscription models endpoint through the API."""
    response = client.get("/api/llm/subscription/openai/models")
    assert response.status_code == 200
    data = response.json()
    assert data == {"vendor": "openai", "models": sorted(OPENAI_CODEX_MODELS)}
    assert "gpt-5.6" in data["models"]
    assert "gpt-5.6-sol" in data["models"]
    assert "gpt-5.6-terra" in data["models"]
    assert "gpt-5.6-luna" in data["models"]
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


OPENROUTER_KEY_PAYLOAD = {
    "data": {
        "label": "sk-or-v1-abc...def",
        "limit": 25.0,
        "limit_remaining": 18.5,
        "limit_reset": None,
        "usage": 6.5,
        "usage_daily": 0.5,
        "usage_weekly": 2.0,
        "usage_monthly": 6.5,
        "is_free_tier": False,
    }
}


@pytest.fixture
def openrouter_settings(monkeypatch):
    """Point the router's settings store at an in-memory OpenRouter config."""
    import httpx

    from openhands.agent_server import llm_router
    from openhands.agent_server.persistence import PersistedSettings
    from openhands.sdk.llm import LLM

    def set_llm(llm: LLM) -> None:
        settings = PersistedSettings()
        settings.agent_settings = settings.agent_settings.model_copy(
            update={"llm": llm}
        )

        class FakeSettingsStore:
            def load(self):
                return settings

        monkeypatch.setattr(
            llm_router, "get_settings_store", lambda config: FakeSettingsStore()
        )

    def set_upstream(handler) -> None:
        real_async_client = httpx.AsyncClient

        def fake_async_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    return set_llm, set_upstream


def test_balance_endpoint_openrouter_success(client, openrouter_settings):
    """Balance endpoint returns OpenRouter credit info without leaking the key."""
    import httpx

    from openhands.sdk.llm import LLM

    set_llm, set_upstream = openrouter_settings
    set_llm(LLM(model="openrouter/moonshotai/kimi-k3", api_key="sk-or-test-key"))

    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json=OPENROUTER_KEY_PAYLOAD)

    set_upstream(handler)

    response = client.get("/api/llm/balance")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openrouter",
        "limit": 25.0,
        "limit_remaining": 18.5,
        "usage": 6.5,
        "usage_daily": 0.5,
        "usage_weekly": 2.0,
        "usage_monthly": 6.5,
        "is_free_tier": False,
    }
    assert "sk-or-test-key" not in response.text
    assert len(seen_requests) == 1
    assert str(seen_requests[0].url) == "https://openrouter.ai/api/v1/key"
    assert seen_requests[0].headers["Authorization"] == "Bearer sk-or-test-key"


def test_balance_endpoint_detects_openrouter_by_base_url(client, openrouter_settings):
    """OpenRouter is detected from base_url even without a model prefix."""
    import httpx

    from openhands.sdk.llm import LLM

    set_llm, set_upstream = openrouter_settings
    set_llm(
        LLM(
            model="moonshotai/kimi-k3",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-test-key",
        )
    )
    set_upstream(lambda request: httpx.Response(200, json=OPENROUTER_KEY_PAYLOAD))

    response = client.get("/api/llm/balance")

    assert response.status_code == 200
    assert response.json()["provider"] == "openrouter"


def test_balance_endpoint_unsupported_provider(client, openrouter_settings):
    """Non-OpenRouter providers return 404 with a clear message."""
    from openhands.sdk.llm import LLM

    set_llm, _ = openrouter_settings
    set_llm(LLM(model="gpt-4o", api_key="sk-test"))

    response = client.get("/api/llm/balance")

    assert response.status_code == 404
    assert "not supported" in response.json()["detail"]


def test_balance_endpoint_missing_api_key(client, openrouter_settings):
    """An OpenRouter config without an API key returns 404."""
    from openhands.sdk.llm import LLM

    set_llm, _ = openrouter_settings
    set_llm(LLM(model="openrouter/moonshotai/kimi-k3"))

    response = client.get("/api/llm/balance")

    assert response.status_code == 404
    assert "No API key" in response.json()["detail"]


def test_balance_endpoint_upstream_error(client, openrouter_settings):
    """Upstream OpenRouter failures surface as 502."""
    import httpx

    from openhands.sdk.llm import LLM

    set_llm, set_upstream = openrouter_settings
    set_llm(LLM(model="openrouter/moonshotai/kimi-k3", api_key="sk-or-test-key"))
    set_upstream(lambda request: httpx.Response(401, json={"error": "bad key"}))

    response = client.get("/api/llm/balance")

    assert response.status_code == 502
    # 5xx details are masked by the app-level handler; the raised exception
    # string still carries the upstream status.
    assert "401" in response.json()["exception"]


def test_balance_endpoint_upstream_network_error(client, openrouter_settings):
    """Network-level failures (e.g. timeouts) surface as 502."""
    import httpx

    from openhands.sdk.llm import LLM

    set_llm, set_upstream = openrouter_settings
    set_llm(LLM(model="openrouter/moonshotai/kimi-k3", api_key="sk-or-test-key"))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    set_upstream(handler)

    response = client.get("/api/llm/balance")

    assert response.status_code == 502
    assert "failed" in response.json()["exception"]


def test_balance_endpoint_with_profile(client, monkeypatch, openrouter_settings):
    """The profile query param loads the named profile's LLM."""
    import tempfile
    from pathlib import Path

    import httpx

    from openhands.agent_server import llm_router
    from openhands.sdk.llm import LLM
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore

    _, set_upstream = openrouter_settings
    set_upstream(lambda request: httpx.Response(200, json=OPENROUTER_KEY_PAYLOAD))

    with tempfile.TemporaryDirectory() as tmpdir:
        store = LLMProfileStore(base_dir=Path(tmpdir))
        store.save(
            "my-openrouter",
            LLM(model="openrouter/moonshotai/kimi-k3", api_key="sk-or-test-key"),
            include_secrets=True,
        )
        monkeypatch.setattr(llm_router, "get_llm_profile_store", lambda: store)

        response = client.get("/api/llm/balance?profile=my-openrouter")

    assert response.status_code == 200
    assert response.json()["provider"] == "openrouter"
    assert "sk-or-test-key" not in response.text


def test_balance_endpoint_profile_not_found(client, monkeypatch):
    """An unknown profile name returns 404."""
    import tempfile
    from pathlib import Path

    from openhands.agent_server import llm_router
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(
            llm_router,
            "get_llm_profile_store",
            lambda: LLMProfileStore(base_dir=Path(tmpdir)),
        )

        response = client.get("/api/llm/balance?profile=nope")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


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
