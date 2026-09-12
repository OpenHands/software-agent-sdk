"""Runtime clients for the fixed Unix-socket credential broker protocol."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from fastmcp.client.auth import OAuth

from openhands.sdk.credential import HttpVersionedCredentialBinding
from openhands.sdk.llm.auth.credentials import (
    CredentialStore,
    OAuthCredentials,
    configure_credential_store,
)


@dataclass(frozen=True)
class BrokerClient:
    socket_path: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.token}

    def subscription(self) -> OAuthCredentials:
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=self.socket_path),
            base_url="http://runtime-credentials",
            headers=self.headers,
            timeout=30,
        ) as client:
            response = client.get("/subscription/openai")
            response.raise_for_status()
            return OAuthCredentials.model_validate(response.json())

    def credential_binding(self, name: str) -> HttpVersionedCredentialBinding:
        return HttpVersionedCredentialBinding(
            f"http://runtime-credentials/credential/{quote(name, safe='')}",
            self.headers,
            transport=httpx.AsyncHTTPTransport(uds=self.socket_path),
        )

    @classmethod
    def from_env(cls) -> BrokerClient | None:
        path = os.getenv("OH_RUNTIME_CREDENTIAL_SOCKET")
        token = os.getenv("OH_RUNTIME_CREDENTIAL_TOKEN")
        if not path and not token:
            return None
        if not path or not token:
            raise ValueError("Incomplete runtime credential broker configuration")
        return cls(path, token)


class BrokerCredentialStore(CredentialStore):
    def __init__(self, broker: BrokerClient):
        self.broker = broker

    @property
    def credentials_dir(self) -> Path:
        raise ValueError("Runtime credentials have no local credential directory")

    def get(self, vendor: str) -> OAuthCredentials | None:
        if vendor != "openai":
            raise ValueError("Runtime subscription provider is not granted")
        return self.broker.subscription()

    def save(self, credentials: OAuthCredentials) -> None:
        del credentials
        raise ValueError("Runtime subscription credentials are read-only")

    def delete(self, vendor: str) -> bool:
        del vendor
        raise ValueError("Runtime subscription credentials are read-only")

    def update_tokens(
        self, vendor: str, access_token: str, refresh_token: str | None, expires_in: int
    ) -> OAuthCredentials | None:
        del vendor, access_token, refresh_token, expires_in
        raise ValueError("Subscription refresh is owned by the credential broker")


def configure_runtime_credentials() -> BrokerClient | None:
    broker = BrokerClient.from_env()
    if broker is not None:
        configure_credential_store(lambda: BrokerCredentialStore(broker))
    else:
        configure_credential_store(CredentialStore)
    return broker


class BrokerMCPAuth(OAuth):
    def __init__(self, broker: BrokerClient, name: str):
        super().__init__()
        self.broker = broker
        self.name = name

    async def async_auth_flow(self, request):
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self.broker.socket_path),
            base_url="http://runtime-credentials",
            headers=self.broker.headers,
            timeout=30,
        ) as client:
            response = await client.get("/mcp/" + quote(self.name, safe=""))
            response.raise_for_status()
            request.headers["Authorization"] = (
                "Bearer " + response.json()["access_token"]
            )
        yield request
