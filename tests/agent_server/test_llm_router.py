"""Tests for LLM router."""

import httpx
import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.llm_router import (
    ERR_INVALID_URL,
    ERR_NETWORK_ERROR,
    ERR_NOT_OPENHANDS_INSTALL,
    ERR_OLD_OPENHANDS_INSTALL,
    ERR_PROXY_LOOKS_LIKE_PRODUCT_URL,
    ERR_PROXY_PROBE_FAILED,
    ERR_TIMEOUT,
    _normalize_url,
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
async def test_list_models_filtered_by_openrouter_provider():
    """OpenRouter's verified entries are namespaced ids (the ``openrouter/``
    prefix stripped, e.g. ``deepseek/deepseek-chat``). Those same strings are
    also genuine LiteLLM catalog models routed to their upstream provider, so a
    naive verified-list match would leak the upstream-direct models into the
    OpenRouter response. Every returned model must be an ``openrouter/`` route.
    """
    response = await list_models(provider="openrouter")
    assert len(response.models) > 0
    assert all(m.startswith("openrouter/") for m in response.models), (
        "OpenRouter response must only contain openrouter/-prefixed routes, "
        f"got non-prefixed entries: "
        f"{[m for m in response.models if not m.startswith('openrouter/')]}"
    )
    # A namespaced entry that collides with a deepseek-direct catalog model must
    # resolve to the OpenRouter route, not the DeepSeek-direct model.
    assert "openrouter/deepseek/deepseek-chat" in response.models
    assert "deepseek/deepseek-chat" not in response.models
    assert "deepseek/deepseek-v4-pro" not in response.models
    assert "deepseek/deepseek-v4-flash" not in response.models


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
    assert "openrouter" in response.models


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
    assert "claude-opus-5" in data["models"]["anthropic"]
    assert "deepseek-v4-flash" in data["models"]["deepseek"]
    assert "kimi-k3" in data["models"]["moonshot"]
    assert "claude-opus-5" in data["models"]["openhands"]
    assert "deepseek-v4-flash" in data["models"]["openhands"]
    assert "kimi-k3" in data["models"]["openhands"]


def test_openai_subscription_models_endpoint_integration(client):
    """Test subscription models endpoint through the API."""
    response = client.get("/api/llm/subscription/openai/models")
    assert response.status_code == 200
    data = response.json()
    assert data == {"vendor": "openai", "models": sorted(OPENAI_CODEX_MODELS)}
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


# --- OpenHands LiteLLM proxy discovery -----------------------------------
#
# These tests cover the fallback matrix in OpenHands/OpenHands#17810: each
# failure mode must surface as its own structured error code, so the frontend
# can render distinct copy instead of a bare LiteLLM 405.

_SAAS_APP_URL = "https://app.all-hands.dev"
_SAAS_PROXY_URL = "https://llm-proxy.app.all-hands.dev"


def _install_mock_transport(monkeypatch, handler):
    """Route every httpx.AsyncClient the router opens through ``handler``.

    ``discover_openhands_proxy`` constructs its client with a timeout kwarg, so
    the replacement must accept and preserve ``**kwargs``.
    """
    from openhands.agent_server import llm_router as mod

    original_async_client = httpx.AsyncClient

    def _factory(**kwargs):
        kwargs.pop("transport", None)
        return original_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _factory)


class TestNormalizeUrl:
    """Users copy-paste URLs from browsers, terminals, and each other; the
    endpoint must accept the common variants without punishing typos."""

    def test_adds_https_when_missing(self):
        assert _normalize_url("app.all-hands.dev") == "https://app.all-hands.dev"

    def test_strips_whitespace_and_trailing_slash(self):
        assert (
            _normalize_url("  https://app.all-hands.dev/  ")
            == "https://app.all-hands.dev"
        )

    def test_preserves_http_scheme(self):
        assert _normalize_url("http://localhost:12000") == "http://localhost:12000"

    def test_rejects_empty(self):
        assert _normalize_url("") is None
        assert _normalize_url("   ") is None

    def test_rejects_non_http_scheme(self):
        assert _normalize_url("ftp://example.com") is None

    def test_rejects_scheme_without_host(self):
        assert _normalize_url("https://") is None


def test_discover_proxy_happy_path(client, monkeypatch):
    """Row 1 of the fallback matrix: modern SaaS response, probe OK."""
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/api/v1/web-client/config":
            return httpx.Response(
                200,
                json={
                    "app_mode": "saas",
                    "llm_proxy_base_url": _SAAS_PROXY_URL,
                },
            )
        if request.url.path == "/health/liveliness":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": _SAAS_APP_URL},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "app_url": _SAAS_APP_URL,
        "llm_proxy_base_url": _SAAS_PROXY_URL,
        "verified": True,
        "error": None,
        "error_message": None,
    }
    # Confirm both hops were exercised — the config lookup and the probe.
    assert any(_SAAS_APP_URL in c for c in calls)
    assert any("/health/liveliness" in c for c in calls)


def test_discover_proxy_old_install_missing_field(client, monkeypatch):
    """Row 2: valid WebClientConfig but no llm_proxy_base_url (pre-#577 OHE)."""

    def handler(request):
        if request.url.path == "/api/v1/web-client/config":
            # An old install that predates the enterprise#577 field.
            return httpx.Response(200, json={"app_mode": "self_hosted"})
        # Should not be reached — the probe only runs after a proxy URL is found.
        raise AssertionError(f"unexpected request to {request.url}")

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": "https://openhands.mycorp.example"},
    )
    body = response.json()
    assert body["llm_proxy_base_url"] is None
    assert body["verified"] is False
    assert body["error"] == ERR_OLD_OPENHANDS_INSTALL


def test_discover_proxy_probe_405_looks_like_product_url(client, monkeypatch):
    """Row 3: proxy URL is really a chat product URL — the exact #17803 bug.

    The endpoint MUST surface this as its own error code so the frontend can
    tell the user 'this URL looks like a product URL, not the LiteLLM proxy'
    instead of a bare LiteLLM 405.
    """
    bad_proxy = "https://app.all-hands.dev"

    def handler(request):
        if request.url.path == "/api/v1/web-client/config":
            return httpx.Response(
                200,
                json={
                    "app_mode": "saas",
                    # An admin misconfigured LITE_LLM_API_URL to the product URL.
                    "llm_proxy_base_url": bad_proxy,
                },
            )
        if request.url.path == "/health/liveliness":
            # LiteLLM SaaS responds 405 to unknown routes on the product URL.
            return httpx.Response(405, json={"detail": "Method Not Allowed"})
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": _SAAS_APP_URL},
    )
    body = response.json()
    # The resolved URL is still returned so the UI can display it.
    assert body["llm_proxy_base_url"] == bad_proxy
    assert body["verified"] is False
    assert body["error"] == ERR_PROXY_LOOKS_LIKE_PRODUCT_URL
    assert "product URL" in body["error_message"]


def test_discover_proxy_probe_other_5xx(client, monkeypatch):
    """Row 3 variant: probe fails with a generic HTTP error."""

    def handler(request):
        if request.url.path == "/api/v1/web-client/config":
            return httpx.Response(
                200,
                json={"app_mode": "saas", "llm_proxy_base_url": _SAAS_PROXY_URL},
            )
        if request.url.path == "/health/liveliness":
            return httpx.Response(502)
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": _SAAS_APP_URL},
    )
    body = response.json()
    assert body["verified"] is False
    assert body["error"] == ERR_PROXY_PROBE_FAILED


def test_discover_proxy_config_404_not_openhands(client, monkeypatch):
    """Row 4: URL isn't an OpenHands install at all."""

    def handler(request):
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": "https://example.com"},
    )
    body = response.json()
    assert body["llm_proxy_base_url"] is None
    assert body["verified"] is False
    assert body["error"] == ERR_NOT_OPENHANDS_INSTALL


def test_discover_proxy_config_non_json(client, monkeypatch):
    """Row 4 variant: URL returns 200 but not JSON — e.g. a marketing page."""

    def handler(request):
        return httpx.Response(200, text="<html>hi</html>")

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": "https://example.com"},
    )
    assert response.json()["error"] == ERR_NOT_OPENHANDS_INSTALL


def test_discover_proxy_config_missing_app_mode(client, monkeypatch):
    """Row 4 variant: JSON without WebClientConfig shape."""

    def handler(request):
        return httpx.Response(200, json={"hello": "world"})

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": "https://example.com"},
    )
    assert response.json()["error"] == ERR_NOT_OPENHANDS_INSTALL


def test_discover_proxy_timeout(client, monkeypatch):
    """Row 5: outbound request times out."""

    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": _SAAS_APP_URL},
    )
    body = response.json()
    assert body["verified"] is False
    assert body["error"] == ERR_TIMEOUT


def test_discover_proxy_network_error(client, monkeypatch):
    """Row 5 variant: DNS / connection error."""

    def handler(request):
        raise httpx.ConnectError("nodename nor servname provided")

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": _SAAS_APP_URL},
    )
    body = response.json()
    assert body["verified"] is False
    assert body["error"] == ERR_NETWORK_ERROR


def test_discover_proxy_invalid_url_never_touches_network(client, monkeypatch):
    """Garbage in the app_url must be rejected before we make any outbound call.

    Otherwise the endpoint becomes a trivial SSRF beacon."""
    called = []

    def handler(request):
        called.append(request.url)
        return httpx.Response(200)

    _install_mock_transport(monkeypatch, handler)

    response = client.post(
        "/api/llm/discover-openhands-proxy",
        json={"app_url": "not a url"},
    )
    body = response.json()
    assert body["error"] == ERR_INVALID_URL
    assert called == []
