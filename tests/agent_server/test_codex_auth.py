import asyncio
import base64
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.codex_auth import CodexAuthService, codex_auth_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.agent import ACPAgent
from openhands.sdk.agent.acp_file_credentials import (
    CODEX_AUTH_SECRET_NAME,
    create_file_credential_lifecycle,
)
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm.auth.openai import DeviceCode
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


def jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{payload}.signature"


def tokens(*, expired: bool = False, refresh: str = "refresh-secret") -> dict:
    return {
        "id_token": jwt(
            {
                "email": "user@example.com",
                "https://api.openai.com/auth": {"chatgpt_account_id": "test-account"},
            }
        ),
        "access_token": jwt({"exp": int(time.time()) + (-60 if expired else 3600)}),
        "refresh_token": refresh,
    }


class Provider:
    def __init__(self):
        self.result = None
        self.started = asyncio.Event()
        self.release = None
        self.error = False

    async def start(self):
        return DeviceCode(
            "https://auth.openai.com/codex/device", "ABCD-EFGH", "private-device-id", 1
        )

    async def poll(self, challenge):
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error:
            raise RuntimeError("refresh-secret private-device-id")
        return self.result

    async def refresh(self, refresh_token):
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error:
            raise RuntimeError("refresh-secret")
        return tokens(refresh="rotated-secret")


@pytest.fixture
def auth(tmp_path):
    store = FileSecretsStore(tmp_path / "persist", Cipher("test-encryption-key"))
    provider = Provider()
    service = CodexAuthService(store, tmp_path / "host" / "auth.json", provider)
    return service, store, provider


@pytest.mark.asyncio
async def test_device_login_persists_only_backend_credentials(auth):
    service, store, provider = auth
    challenge = await service.start()
    assert challenge.user_code == "ABCD-EFGH"
    assert "private-device-id" not in challenge.model_dump_json()
    assert (await service.poll(challenge.device_code)).state == "pending"
    provider.result = tokens()
    result = await service.poll(challenge.device_code)
    assert result.connected
    assert "refresh-secret" not in result.model_dump_json()
    saved = json.loads(store.get_secret(CODEX_AUTH_SECRET_NAME))
    assert saved["tokens"] == {**provider.result, "account_id": "test-account"}
    assert "refresh-secret" not in (store.persistence_dir / "secrets.json").read_text()
    restarted = CodexAuthService(store, service.host_auth_file, provider)
    assert (await restarted.status()).connected


@pytest.mark.asyncio
async def test_cancel_and_new_login_invalidate_old_attempt(auth):
    service, store, provider = auth
    first = await service.start()
    second = await service.start()
    provider.result = tokens()
    assert (await service.poll(first.device_code)).state == "cancelled"
    await service.cancel(second.device_code)
    assert (await service.poll(second.device_code)).state == "cancelled"
    assert store.get_secret(CODEX_AUTH_SECRET_NAME) is None


@pytest.mark.asyncio
async def test_logout_cancels_inflight_login(auth):
    service, store, provider = auth
    provider.release = asyncio.Event()
    provider.result = tokens()
    challenge = await service.start()
    polling = asyncio.create_task(service.poll(challenge.device_code))
    await provider.started.wait()
    await service.logout()
    assert not (await polling).connected
    assert store.get_secret(CODEX_AUTH_SECRET_NAME) is None


@pytest.mark.asyncio
async def test_expiry_and_failure_are_safe(auth, monkeypatch):
    service, store, provider = auth
    challenge = await service.start()
    monkeypatch.setattr(
        "openhands.agent_server.codex_auth.time.time",
        lambda: challenge.expires_at / 1000 + 1,
    )
    assert (await service.poll(challenge.device_code)).state == "expired"
    monkeypatch.undo()
    challenge = await service.start()
    provider.error = True
    result = await service.poll(challenge.device_code)
    assert result.state == "error"
    assert "refresh-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_import_refresh_and_disconnect_host_login(auth):
    service, store, provider = auth
    service.host_auth_file.parent.mkdir()
    service.host_auth_file.write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": tokens(expired=True)})
    )
    assert (await service.status()).connected
    assert (
        json.loads(store.get_secret(CODEX_AUTH_SECRET_NAME))["tokens"]["refresh_token"]
        == "rotated-secret"
    )
    await service.logout()
    assert not service.host_auth_file.exists()
    assert not (await service.status()).connected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_refresh_cannot_overwrite_newer_credentials(auth, failure):
    service, store, provider = auth
    store.set_secret(
        CODEX_AUTH_SECRET_NAME, json.dumps({"tokens": tokens(expired=True)})
    )
    provider.release = asyncio.Event()
    provider.error = failure
    refreshing = asyncio.create_task(service.status())
    await provider.started.wait()
    newer = json.dumps({"tokens": tokens(refresh="newer-secret")})
    store.set_secret(CODEX_AUTH_SECRET_NAME, newer)
    provider.release.set()
    assert (await refreshing).connected
    assert store.get_secret(CODEX_AUTH_SECRET_NAME) == newer


@pytest.mark.asyncio
async def test_failed_refresh_disconnects_without_deleting_newer_login(auth):
    service, store, provider = auth
    store.set_secret(
        CODEX_AUTH_SECRET_NAME, json.dumps({"tokens": tokens(expired=True)})
    )
    provider.error = True
    assert not (await service.status()).connected
    assert store.get_secret(CODEX_AUTH_SECRET_NAME) is None


def test_http_contract_returns_safe_metadata(auth):
    service, store, provider = auth
    app = FastAPI()
    app.state.codex_auth = service
    app.include_router(codex_auth_router, prefix="/api")
    with TestClient(app) as client:
        assert client.get("/api/acp/codex/auth/status").json()["connected"] is False
        challenge = client.post("/api/acp/codex/auth/device/start").json()
        provider.result = tokens()
        response = client.post(
            "/api/acp/codex/auth/device/poll",
            json={"device_code": challenge["device_code"]},
        )
        assert response.json()["connected"] is True
        assert "refresh-secret" not in response.text
        assert client.post("/api/acp/codex/auth/logout").json()["connected"] is False


@pytest.mark.asyncio
async def test_login_provisions_new_codex_conversation_without_manual_secrets(
    auth, tmp_path
):
    auth_service, store, provider = auth
    challenge = await auth_service.start()
    provider.result = tokens()
    await auth_service.poll(challenge.device_code)
    request = StartConversationRequest(
        agent=ACPAgent(acp_server="codex", acp_command=["codex-acp"]),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )
    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        secrets_store=store,
    ) as conversations:
        info, _ = await conversations.start_conversation(request)
        runtime = await conversations.get_event_service(info.id)
        assert runtime is not None
        binding = runtime.credential_bindings[CODEX_AUTH_SECRET_NAME]
        lifecycle = create_file_credential_lifecycle(
            CODEX_AUTH_SECRET_NAME, binding, asyncio.run
        )
        assert lifecycle is not None
        registry = SecretRegistry()
        env = {}
        try:
            await asyncio.to_thread(lifecycle.materialize, registry, env)
            materialized = json.loads(
                (Path(env["CODEX_HOME"]) / "auth.json").read_text()
            )
            assert materialized["tokens"] == {
                **provider.result,
                "account_id": "test-account",
            }
            assert "OPENAI_API_KEY" not in env
            assert registry.mask_secrets_in_output("refresh-secret") != "refresh-secret"
        finally:
            await asyncio.to_thread(lifecycle.close)


@pytest.mark.asyncio
async def test_new_login_can_replace_invalid_manual_credentials(auth):
    service, store, provider = auth
    store.set_secret(CODEX_AUTH_SECRET_NAME, "invalid-json")
    challenge = await service.start()
    provider.result = tokens()
    assert (await service.poll(challenge.device_code)).connected


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["", "**********", "not-a-jwt"])
async def test_invalid_id_credentials_never_report_connected(auth, invalid):
    service, store, provider = auth
    challenge = await service.start()
    provider.result = {**tokens(), "id_token": invalid}
    assert (await service.poll(challenge.device_code)).state == "error"
    assert store.get_secret(CODEX_AUTH_SECRET_NAME) is None
