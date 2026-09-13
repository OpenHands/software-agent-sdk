import time
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.broker import RuntimeCredentialBroker
from openhands.agent_server.docker_runtime.provisioning import (
    RuntimeGrants,
    RuntimeProvisioningStore,
)
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.llm.auth.credentials import CredentialStore, OAuthCredentials


@pytest.mark.asyncio
async def test_socket_broker_grants_are_isolated_and_revocable(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(
        secret_key=SecretStr("outer-key"),
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
    )
    store = RuntimeProvisioningStore(config)
    first, second = store.create(uuid4()), store.create(uuid4())
    first = first.model_copy(
        update={
            "grants": RuntimeGrants(
                subscription="openai", credential_names=frozenset({"ALLOWED"})
            )
        }
    )
    store.save(first)
    credentials = CredentialStore(tmp_path / "global" / "auth")
    credentials.save(
        OAuthCredentials(
            vendor="openai",
            access_token="selected-access",
            refresh_token="outer-refresh-only",
            expires_at=int(time.time() * 1000) + 300000,
        )
    )
    secrets = FileSecretsStore(tmp_path / "global", cipher=config.cipher)
    secrets.set_secret("ALLOWED", "selected-secret")
    secrets.set_secret("UNRELATED", "never-granted")
    brokers = [
        RuntimeCredentialBroker(store, identity.conversation_id, credentials, secrets)
        for identity in (first, second)
    ]
    try:
        for broker in brokers:
            await broker.start()
        transport = httpx.AsyncHTTPTransport(
            uds=str(brokers[0].socket_dir / "credentials.sock")
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://broker",
            headers={
                "Authorization": "Bearer " + first.broker_token.get_secret_value()
            },
        ) as client:
            response = await client.get("/subscription/openai")
            assert response.status_code == 200, response.text
            assert response.json()["access_token"] == "selected-access"
            assert response.json()["refresh_token"] == ""
            assert (await client.get("/credential/ALLOWED")).json()[
                "value"
            ] == "selected-secret"
            assert (await client.get("/credential/UNRELATED")).status_code == 403
            assert (await client.get("/subscription/another")).status_code == 404
            assert (
                await client.get(
                    "/credential/ALLOWED",
                    headers={
                        "Authorization": "Bearer "
                        + second.broker_token.get_secret_value()
                    },
                )
            ).status_code == 401
            store.save(first.model_copy(update={"grants": RuntimeGrants()}))
            assert (await client.get("/subscription/openai")).status_code == 403
            assert (await client.get("/credential/ALLOWED")).status_code == 403
        assert set(path.name for path in brokers[0].socket_dir.iterdir()) == {
            "credentials.sock"
        }
        assert brokers[0].socket_dir != brokers[1].socket_dir
        stored = credentials.get("openai")
        assert stored is not None
        assert stored.refresh_token == "outer-refresh-only"
    finally:
        for broker in brokers:
            await broker.close()
