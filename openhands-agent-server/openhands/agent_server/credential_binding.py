from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openhands.agent_server.event_service import CredentialBindingActivationTooLate
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.agent.acp_file_credentials import CODEX_AUTH_SECRET_NAME
from openhands.sdk.credential import (
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
    renewal_url: str | None = Field(default=None, min_length=1, max_length=4096)
    renewal_interval_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    authorization_expires_in_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        if len(headers) > 16 or any(
            not name or len(name) > 256 or not value or len(value) > 8192
            for name, value in headers.items()
        ):
            raise ValueError("Invalid credential binding headers")
        return headers

    @model_validator(mode="after")
    def validate_renewal(self) -> CredentialBindingActivation:
        values = (
            self.renewal_url,
            self.renewal_interval_seconds,
            self.authorization_expires_in_seconds,
        )
        if any(value is None for value in values) and any(
            value is not None for value in values
        ):
            raise ValueError("renewal configuration must be provided together")
        return self


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
                "ChatGPT authentication is missing. Please sign in again."
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
                "ChatGPT authentication is missing. Please sign in again."
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
    if secret_name != CODEX_AUTH_SECRET_NAME:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        await request.app.state.conversation_service.activate_credential_binding(
            conversation_id,
            secret_name,
            HttpVersionedCredentialBinding(
                activation.url,
                activation.headers,
                renewal_url=activation.renewal_url,
                renewal_interval_seconds=activation.renewal_interval_seconds,
                authorization_expires_in_seconds=(
                    activation.authorization_expires_in_seconds
                ),
            ),
        )
    except CredentialBindingActivationTooLate as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credential binding activation arrived after ACP initialization",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
