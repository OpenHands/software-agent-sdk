from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from openhands.agent_server.event_service import CredentialBindingActivationTooLate
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.agent.acp_file_credentials import supports_file_credential_binding
from openhands.sdk.credential import (
    CredentialBindingError,
    CredentialConflict,
    CredentialNeedsReauthentication,
    CredentialSyncError,
    HttpVersionedCredentialBinding,
    ResolvedCredential,
)


class CredentialBindingActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        if len(headers) > 16 or any(
            not name or len(name) > 256 or not value or len(value) > 8192
            for name, value in headers.items()
        ):
            raise ValueError("Invalid credential binding headers")
        return headers


@dataclass(frozen=True)
class LocalVersionedCredentialBinding:
    store: FileSecretsStore
    secret_name: str

    async def load(self) -> ResolvedCredential:
        try:
            value, version = await asyncio.to_thread(
                self.store.load_versioned_secret,
                self.secret_name,
            )
        except KeyError as exc:
            raise CredentialNeedsReauthentication(
                "Credential is missing. Please authenticate again."
            ) from exc
        except Exception as exc:
            raise CredentialSyncError("Local credential store is unavailable.") from exc
        return ResolvedCredential(value=value, version=version)

    async def replace(self, expected_version: str, value: str) -> str:
        try:
            return await asyncio.to_thread(
                self.store.replace_versioned_secret,
                self.secret_name,
                expected_version,
                value,
            )
        except KeyError as exc:
            raise CredentialNeedsReauthentication(
                "Credential is missing. Please authenticate again."
            ) from exc
        except ValueError as exc:
            raise CredentialConflict(
                "The canonical credential changed in another runtime."
            ) from exc
        except Exception as exc:
            raise CredentialSyncError("Local credential update failed.") from exc


router = APIRouter(prefix="/conversations", tags=["Credential Bindings"])


@router.put(
    "/{conversation_id}/credential-bindings/{secret_name}",
    include_in_schema=False,
)
async def activate_credential_binding(
    conversation_id: UUID,
    secret_name: str,
    activation: CredentialBindingActivation,
    request: Request,
) -> Response:
    if not supports_file_credential_binding(secret_name):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    binding = HttpVersionedCredentialBinding(
        activation.url,
        activation.headers,
    )
    try:
        await binding.load()
        await request.app.state.conversation_service.activate_credential_binding(
            conversation_id,
            secret_name,
            binding,
        )
    except CredentialBindingActivationTooLate as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credential binding activation arrived after ACP initialization",
        ) from exc
    except CredentialBindingError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Credential binding source is unavailable",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
