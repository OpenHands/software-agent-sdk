from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

import openhands.sdk.llm.auth.openai as openai_auth
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.broker import RuntimeCredentialBroker
from openhands.agent_server.docker_runtime.provisioning import (
    RuntimeGrants,
    RuntimeProvisioningStore,
)
from openhands.agent_server.persistence import (
    FileSecretsStore,
    PersistedSettings,
    get_settings_store,
    reset_stores,
)
from openhands.sdk.llm.auth.credentials import CredentialStore, OAuthCredentials
from openhands.sdk.mcp.config import MCPOAuthAuthCredential, coerce_mcp_config


class OAuthRefreshServer:
    def __init__(self, path: str, refresh_token: str, response: dict[str, object]):
        self.path = path
        self.refresh_token = refresh_token
        self.response = response
        self.requests: list[dict[str, list[str]]] = []
        self._lock = threading.Lock()

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                form = parse_qs(self.rfile.read(length).decode())
                with owner._lock:
                    owner.requests.append(form)
                if (
                    self.path != owner.path
                    or form.get("grant_type") != ["refresh_token"]
                    or form.get("refresh_token") != [owner.refresh_token]
                ):
                    self.send_response(400)
                    self.end_headers()
                    return
                time.sleep(0.05)
                body = json.dumps(owner.response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> OAuthRefreshServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    persistence = tmp_path / "global"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(persistence))
    monkeypatch.setenv("OH_SECRET_KEY", "outer-key")
    reset_stores()
    return Config(
        secret_key=SecretStr("outer-key"),
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
    )


async def _broker_client(
    broker: RuntimeCredentialBroker, token: str
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(
            uds=str(broker.socket_dir / "credentials.sock")
        ),
        base_url="http://runtime-credentials",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_native_expired_access_refreshes_once_and_keeps_refresh_outer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _config(tmp_path, monkeypatch)
    credentials = CredentialStore(tmp_path / "global" / "auth")
    credentials.save(
        OAuthCredentials(
            vendor="openai",
            access_token="expired-access",
            refresh_token="native-refresh",
            expires_at=0,
        )
    )
    with OAuthRefreshServer(
        "/oauth/token",
        "native-refresh",
        {
            "access_token": "fresh-native-access",
            "refresh_token": "rotated-native-refresh",
            "expires_in": 3600,
        },
    ) as oauth:
        monkeypatch.setattr(openai_auth, "ISSUER", oauth.base_url)
        store = RuntimeProvisioningStore(config)
        identity = store.create(uuid4()).model_copy(
            update={"grants": RuntimeGrants(subscription="openai")}
        )
        store.save(identity)
        broker = RuntimeCredentialBroker(
            store,
            identity.conversation_id,
            credentials,
            FileSecretsStore(tmp_path / "global", cipher=config.cipher),
        )
        await broker.start()
        try:
            client = await _broker_client(
                broker, identity.broker_token.get_secret_value()
            )
            async with client:
                responses = await asyncio.gather(
                    *(client.get("/subscription/openai") for _ in range(8))
                )
            assert all(response.status_code == 200 for response in responses)
            assert {response.json()["access_token"] for response in responses} == {
                "fresh-native-access"
            }
            assert {response.json()["refresh_token"] for response in responses} == {""}
            assert len(oauth.requests) == 1
            stored = credentials.get("openai")
            assert stored is not None
            assert stored.access_token == "fresh-native-access"
            assert stored.refresh_token == "rotated-native-refresh"
            assert stored.expires_at > int(time.time() * 1000)
        finally:
            await broker.close()
            reset_stores()


@pytest.mark.asyncio
async def test_selected_mcp_expired_access_refreshes_once_and_persists_outer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=2))
    config = _config(tmp_path, monkeypatch)
    with OAuthRefreshServer(
        "/token",
        "mcp-refresh",
        {
            "access_token": "fresh-mcp-access",
            "refresh_token": "rotated-mcp-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    ) as oauth:
        settings = PersistedSettings()
        settings.agent_settings = settings.agent_settings.model_copy(
            update={
                "mcp_config": coerce_mcp_config(
                    {
                        "selected": {
                            "url": f"{oauth.base_url}/mcp",
                            "transport": "streamable-http",
                            "auth": {
                                "strategy": "oauth2",
                                "authentication": {
                                    "type": "oauth",
                                    "client_auth_method": "none",
                                },
                                "state": {
                                    "tokens": {
                                        "access_token": "expired-mcp-access",
                                        "refresh_token": "mcp-refresh",
                                        "token_type": "Bearer",
                                        "expires_in": 3600,
                                    },
                                    "client_info": {
                                        "redirect_uris": [
                                            "http://127.0.0.1:64801/callback"
                                        ],
                                        "client_id": "mcp-client",
                                        "token_endpoint_auth_method": "none",
                                    },
                                    "token_expires_at": time.time() - 3600,
                                },
                            },
                        }
                    }
                )
            }
        )
        settings_store = get_settings_store(config)
        settings_store.save(settings)
        store = RuntimeProvisioningStore(config)
        identity = store.create(uuid4()).model_copy(
            update={"grants": RuntimeGrants(mcp_server_names=frozenset({"selected"}))}
        )
        store.save(identity)
        broker = RuntimeCredentialBroker(
            store,
            identity.conversation_id,
            CredentialStore(tmp_path / "global" / "auth"),
            FileSecretsStore(tmp_path / "global", cipher=config.cipher),
        )
        await broker.start()
        try:
            client = await _broker_client(
                broker, identity.broker_token.get_secret_value()
            )
            async with client:
                responses = await asyncio.gather(
                    *(client.get("/mcp/selected") for _ in range(8))
                )
            assert all(response.status_code == 200 for response in responses)
            assert {response.json()["access_token"] for response in responses} == {
                "fresh-mcp-access"
            }
            assert all("refresh_token" not in response.json() for response in responses)
            assert len(oauth.requests) == 1
            loaded = settings_store.load()
            assert loaded is not None
            auth = loaded.agent_settings.mcp_config["selected"].auth
            assert isinstance(auth, MCPOAuthAuthCredential)
            state = auth.state
            assert state is not None and state.tokens is not None
            assert state.tokens.access_token is not None
            assert state.tokens.refresh_token is not None
            assert state.tokens.access_token.get_secret_value() == "fresh-mcp-access"
            assert (
                state.tokens.refresh_token.get_secret_value() == "rotated-mcp-refresh"
            )
            assert state.token_expires_at is not None
            assert state.token_expires_at > time.time()
        finally:
            await broker.close()
            reset_stores()
