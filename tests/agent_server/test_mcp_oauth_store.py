from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.client.auth import OAuth
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.settings import ClientRegistrationOptions
from pydantic import SecretStr

import openhands.sdk.mcp.utils as mcp_utils
from openhands.agent_server.config import Config
from openhands.agent_server.mcp_oauth_store import (
    MCPSettingsOAuthTokenStore,
    create_settings_backed_mcp_tool_provider,
)
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_settings_store,
    reset_stores,
)
from openhands.sdk.mcp.config import MCPConfig


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def _wait_for_http_server(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=0.2) as client:
                client.get(f"http://127.0.0.1:{port}/")
            return
        except httpx.ConnectError:
            time.sleep(0.05)
        except Exception:
            return
    raise RuntimeError(f"Timed out waiting for test MCP server on port {port}")


class _HeadlessOAuth(OAuth):
    """FastMCP OAuth client that follows the authorization redirect itself.

    This preserves the real DCR/PKCE/token exchange while avoiding an external
    browser and local callback server in CI.
    """

    reject_redirects = False
    redirect_count = 0

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("callback_port", _find_free_port())
        super().__init__(*args, **kwargs)
        self._redirect_location: str | None = None

    async def redirect_handler(self, authorization_url: str) -> None:
        type(self).redirect_count += 1
        if type(self).reject_redirects:
            raise AssertionError("OAuth redirect should not run with stored tokens")
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(authorization_url)
        assert response.status_code in {302, 303, 307, 308}
        self._redirect_location = response.headers["location"]

    async def callback_handler(self) -> tuple[str, str | None]:
        assert self._redirect_location is not None
        query = parse_qs(urlparse(self._redirect_location).query)
        code = query.get("code", [None])[0]
        assert code is not None
        return code, query.get("state", [None])[0]


@pytest.fixture
def protected_oauth_mcp_server():
    port = _find_free_port()
    provider = InMemoryOAuthProvider(
        base_url=f"http://127.0.0.1:{port}",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mail.read"],
            default_scopes=["mail.read"],
        ),
        required_scopes=["mail.read"],
    )
    mcp = FastMCP("protected-oauth-mcp", auth=provider)

    @mcp.tool()
    def read_subject(subject: str) -> str:
        return f"OAuth mail subject: {subject}"

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            mcp.run_http_async(
                host="127.0.0.1",
                port=port,
                transport="http",
                show_banner=False,
                path="/mcp",
            )
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    _wait_for_http_server(port)
    yield f"http://127.0.0.1:{port}/mcp"


@pytest.mark.asyncio
async def test_mcp_oauth_token_store_persists_values_in_settings(
    tmp_path: Path,
):
    reset_stores()
    try:
        config = Config(
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("mcp-oauth-test-key"),
        )
        settings = PersistedSettings()
        settings.agent_settings = settings.agent_settings.model_copy(
            update={
                "mcp_config": MCPConfig.model_validate(
                    {
                        "mcpServers": {
                            "superhuman": {
                                "url": "https://mcp.example.com/mcp",
                                "auth": {
                                    "strategy": "oauth2",
                                    "authentication": {
                                        "type": "oauth",
                                        "client_auth_method": "none",
                                    },
                                },
                            }
                        }
                    }
                )
            }
        )
        settings_store = get_settings_store(config)
        settings_store.save(settings)
        create_settings_backed_mcp_tool_provider(config)

        key = "https://mcp.example.com/mcp/tokens"
        client_info_key = "https://mcp.example.com/mcp/client_info"
        token_expiry_key = "https://mcp.example.com/mcp/token_expiry"
        value = {
            "access_token": "super-secret-token",
            "refresh_token": "refresh-token",
        }
        client_info = {
            "redirect_uris": ["http://127.0.0.1:64801/callback"],
            "client_id": "superhuman-client",
            "client_secret": "superhuman-client-secret",
        }
        token_expiry = {"expires_at": 12345.0}

        store = MCPSettingsOAuthTokenStore()
        await store.put(key=key, value=value, collection="mcp-oauth-token")
        await store.put(
            key=client_info_key,
            value=client_info,
            collection="mcp-oauth-client-info",
        )
        await store.put(
            key=token_expiry_key,
            value=token_expiry,
            collection="mcp-oauth-token-expiry",
        )

        reloaded_store = MCPSettingsOAuthTokenStore()
        assert (
            await reloaded_store.get(key=key, collection="mcp-oauth-token")
        ) == value
        assert (
            await reloaded_store.get(
                key=client_info_key,
                collection="mcp-oauth-client-info",
            )
        ) == client_info
        assert (
            await reloaded_store.get(
                key=token_expiry_key,
                collection="mcp-oauth-token-expiry",
            )
        ) == token_expiry

        on_disk_text = (tmp_path / ".openhands" / "settings.json").read_text()
        assert "super-secret-token" not in on_disk_text
        assert "refresh-token" not in on_disk_text
        assert "superhuman-client-secret" not in on_disk_text

        on_disk = json.loads(on_disk_text)
        stored_state = on_disk["agent_settings"]["mcp_config"]["mcpServers"][
            "superhuman"
        ]["auth"]["state"]
        stored_value = stored_state["tokens"]
        assert stored_value["access_token"].startswith("gAAAA")
        assert stored_value["refresh_token"].startswith("gAAAA")
        assert stored_state["client_info"]["client_secret"].startswith("gAAAA")
        assert stored_state["token_expires_at"] == 12345.0

        loaded = settings_store.load()
        assert loaded is not None
        assert loaded.agent_settings.mcp_config is not None
        server = loaded.agent_settings.mcp_config.model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            by_alias=True,
            exclude_none=True,
            exclude_defaults=True,
        )["mcpServers"]["superhuman"]
        assert server["auth"]["state"] == {
            "tokens": value,
            "client_info": client_info,
            "token_expires_at": 12345.0,
        }
    finally:
        reset_stores()


def test_oauth_mcp_connection_persists_and_reuses_settings_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_oauth_mcp_server: str,
):
    """Authenticate to a protected MCP server once, serialize, reload, and reuse.

    The server uses FastMCP's in-memory OAuth provider. The client uses the
    normal SDK MCP settings shape plus FastMCP OAuth; only the human
    browser/callback step is replaced with a deterministic redirect follower.
    """

    reset_stores()
    _HeadlessOAuth.redirect_count = 0
    _HeadlessOAuth.reject_redirects = False
    monkeypatch.setattr(mcp_utils, "OAuth", _HeadlessOAuth)
    try:
        config = Config(
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("mcp-oauth-e2e-test-key"),
        )
        mcp_config = MCPConfig.model_validate(
            {
                "mcpServers": {
                    "mail": {
                        "url": protected_oauth_mcp_server,
                        "type": "http",
                        "auth": {
                            "strategy": "oauth2",
                            "authentication": {
                                "type": "oauth",
                                "client_auth_method": "none",
                                "scopes": ["mail.read"],
                            },
                        },
                    }
                }
            }
        )
        settings = PersistedSettings()
        settings.agent_settings = settings.agent_settings.model_copy(
            update={"mcp_config": mcp_config}
        )
        settings_store = get_settings_store(config)
        settings_store.save(settings)
        tool_provider = create_settings_backed_mcp_tool_provider(config)

        with tool_provider.create_tools(
            mcp_config,
            timeout=10.0,
        ) as client:
            tool = next(tool for tool in client.tools if tool.name == "read_subject")
            assert tool.executor is not None
            observation = tool.executor(
                tool.action_from_arguments({"subject": "Quarterly Plan"})
            )
            assert "OAuth mail subject: Quarterly Plan" in observation.text

        assert _HeadlessOAuth.redirect_count == 1
        on_disk_text = (tmp_path / ".openhands" / "settings.json").read_text()
        assert "test_access_token_" not in on_disk_text
        assert "test_refresh_token_" not in on_disk_text

        reloaded = settings_store.load()
        assert reloaded is not None
        assert reloaded.agent_settings.mcp_config is not None
        persisted_mcp_config = reloaded.agent_settings.mcp_config
        server = persisted_mcp_config.model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            by_alias=True,
            exclude_none=True,
            exclude_defaults=True,
        )["mcpServers"]["mail"]
        state = server["auth"]["state"]
        assert state["tokens"]["access_token"].startswith("test_access_token_")
        assert state["tokens"]["refresh_token"].startswith("test_refresh_token_")
        assert state["client_info"]["client_id"]

        _HeadlessOAuth.reject_redirects = True
        with tool_provider.create_tools(
            persisted_mcp_config,
            timeout=10.0,
        ) as client:
            tool = next(tool for tool in client.tools if tool.name == "read_subject")
            assert tool.executor is not None
            observation = tool.executor(
                tool.action_from_arguments({"subject": "Follow-up"})
            )
            assert "OAuth mail subject: Follow-up" in observation.text

        assert _HeadlessOAuth.redirect_count == 1
    finally:
        reset_stores()


@pytest.mark.asyncio
async def test_mcp_oauth_token_storage_does_not_attach_to_non_oauth_server(
    tmp_path: Path,
):
    reset_stores()
    try:
        config = Config(
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("mcp-oauth-test-key"),
        )
        settings = PersistedSettings()
        settings.agent_settings = settings.agent_settings.model_copy(
            update={
                "mcp_config": MCPConfig.model_validate(
                    {
                        "mcpServers": {
                            "plain": {
                                "url": "https://mcp.example.com/mcp",
                            }
                        }
                    }
                )
            }
        )
        settings_store = get_settings_store(config)
        settings_store.save(settings)

        create_settings_backed_mcp_tool_provider(config)
        store = MCPSettingsOAuthTokenStore()

        await store.put(
            key="https://mcp.example.com/mcp/tokens",
            value={"access_token": "super-secret-token"},
            collection="mcp-oauth-token",
        )

        loaded = settings_store.load()
        assert loaded is not None
        assert loaded.agent_settings.mcp_config is not None
        server = loaded.agent_settings.mcp_config.model_dump(
            by_alias=True, exclude_none=True, exclude_defaults=True
        )["mcpServers"]["plain"]
        assert "auth" not in server
    finally:
        reset_stores()
