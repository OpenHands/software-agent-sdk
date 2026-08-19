"""Provider connection endpoints for sharing LLM credentials across profiles.

A provider connection is a shared ``api_key`` + optional ``base_url`` that one
or more LLM profiles reference by id. The credential is resolved into a runnable
:class:`~openhands.sdk.llm.llm.LLM` lazily, at profile-load time
(:meth:`LLMProfileStore.load`) — this router only performs CRUD over the stored
connections. Because resolution is read-at-use, rotating a key here takes effect
the next time a linked profile is activated or launched; nothing is copied into
active settings, so there is no separate refresh path to keep in sync.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from openhands.agent_server._secrets_exposure import (
    get_cipher,
    get_config,
    store_errors,
)
from openhands.agent_server.persistence import (
    ProviderConnection,
    get_llm_profile_store,
    get_provider_connections_store,
    get_settings_store,
)
from openhands.sdk.llm.provider_connection_store import (
    ProviderConnectionLimitExceeded,
    ProviderConnectionNotFound,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

provider_connections_router = APIRouter(
    prefix="/llm/provider-connections", tags=["LLM Provider Connections"]
)


def _now() -> int:
    return int(time.time())


class ProviderConnectionCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(default="custom", min_length=1, max_length=128)
    api_key: SecretStr = Field(..., min_length=1)
    base_url: str | None = Field(default=None, max_length=2048)

    model_config = ConfigDict(extra="forbid")


class ProviderConnectionUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: SecretStr | None = None
    base_url: str | None = Field(default=None, max_length=2048)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _reject_null_required_fields(self) -> ProviderConnectionUpdateRequest:
        # Only `base_url` may be set to null (to clear it). `display_name` and
        # `provider` are required on the stored model; accepting explicit null on
        # PATCH would persist a null that poisons every subsequent store read.
        for field in ("display_name", "provider"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be set to null")
        return self


class ProviderConnectionResponse(BaseModel):
    id: str
    display_name: str
    provider: str
    base_url: str | None = None
    created_at: int
    updated_at: int
    api_key_set: bool = False


def _to_response(connection: ProviderConnection) -> ProviderConnectionResponse:
    return ProviderConnectionResponse(
        id=connection.id,
        display_name=connection.display_name,
        provider=connection.provider,
        base_url=connection.base_url,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        api_key_set=connection.api_key_value() is not None,
    )


def _linked_profile_names(connection_id: str) -> list[str]:
    return sorted(
        str(summary["name"])
        for summary in get_llm_profile_store().list_summaries()
        if summary.get("provider_connection_id") == connection_id
    )


def _active_settings_references_connection(config, connection_id: str) -> bool:
    settings = get_settings_store(config).load()
    if settings is None:
        return False
    return settings.agent_settings.llm.provider_connection_id == connection_id


def _raise_if_connection_is_referenced(config, connection_id: str) -> None:
    """Block deletion while any profile or the active settings still point here.

    Deleting a referenced connection would leave those references dangling; a
    linked profile with no inline key raises on its next load. So the delete is
    rejected until the references are removed first.
    """
    profile_names = _linked_profile_names(connection_id)
    active_reference = _active_settings_references_connection(config, connection_id)
    if not profile_names and not active_reference:
        return

    reasons = []
    if profile_names:
        reasons.append(f"referenced by LLM profile(s): {', '.join(profile_names)}")
    if active_reference:
        reasons.append("referenced by the active agent settings")
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Provider connection cannot be deleted while it is "
            + " and ".join(reasons)
            + ". Update those references before deleting it."
        ),
    )


@provider_connections_router.get("", response_model=list[ProviderConnectionResponse])
async def list_provider_connections(
    request: Request,
) -> list[ProviderConnectionResponse]:
    cipher = get_cipher(request)
    store = get_provider_connections_store(get_config(request))
    with store_errors():
        connections = store.list(cipher=cipher)
    return [_to_response(c) for c in connections]


@provider_connections_router.post(
    "", response_model=ProviderConnectionResponse, status_code=status.HTTP_201_CREATED
)
async def create_provider_connection(
    request: Request, body: ProviderConnectionCreateRequest
) -> ProviderConnectionResponse:
    cipher = get_cipher(request)
    store = get_provider_connections_store(get_config(request))
    now = _now()
    connection = ProviderConnection(
        id=uuid.uuid4().hex,
        display_name=body.display_name,
        provider=body.provider,
        api_key=body.api_key,
        base_url=body.base_url,
        created_at=now,
        updated_at=now,
    )
    try:
        with store_errors():
            store.create(connection, cipher=cipher)
    except ProviderConnectionLimitExceeded as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{e} Delete one before adding another.",
        )
    logger.info(
        "Created LLM provider connection", extra={"connection_id": connection.id}
    )
    return _to_response(connection)


@provider_connections_router.patch(
    "/{connection_id}", response_model=ProviderConnectionResponse
)
async def update_provider_connection(
    request: Request, connection_id: str, body: ProviderConnectionUpdateRequest
) -> ProviderConnectionResponse:
    fields = body.model_fields_set
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one provider connection field to update",
        )

    cipher = get_cipher(request)
    store = get_provider_connections_store(get_config(request))
    with store_errors():
        connection = store.get(connection_id, cipher=cipher)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider connection '{connection_id}' not found",
        )

    # A connection must always have a key, so clearing it is not a valid
    # update. Reject api_key: null explicitly instead of silently dropping it.
    if "api_key" in fields and body.api_key is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="api_key cannot be cleared; provide a new key to rotate it",
        )

    updates: dict[str, Any] = {"updated_at": _now()}
    for field in ("display_name", "provider", "base_url"):
        if field in fields:
            updates[field] = getattr(body, field)
    if "api_key" in fields:
        updates["api_key"] = body.api_key
    updated = connection.model_copy(update=updates)

    # store_errors() maps infra failures (lock timeout -> 503, corrupted/
    # wrong-cipher file -> 4xx); the inner except keeps the deleted-between-
    # get-and-update race as a 404 rather than the 422 store_errors would give
    # ProviderConnectionNotFound.
    with store_errors():
        try:
            store.update(updated, cipher=cipher)
        except ProviderConnectionNotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider connection '{connection_id}' not found",
            )
    logger.info(
        "Updated LLM provider connection", extra={"connection_id": connection_id}
    )
    return _to_response(updated)


@provider_connections_router.delete(
    "/{connection_id}", response_model=ProviderConnectionResponse
)
async def delete_provider_connection(
    request: Request, connection_id: str
) -> ProviderConnectionResponse:
    config = get_config(request)
    cipher = get_cipher(request)
    store = get_provider_connections_store(config)
    with store_errors():
        connection = store.get(connection_id, cipher=cipher)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider connection '{connection_id}' not found",
        )
    _raise_if_connection_is_referenced(config, connection_id)

    # See update handler: keep the delete-race as 404, map infra errors.
    with store_errors():
        try:
            store.delete(connection_id, cipher=cipher)
        except ProviderConnectionNotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider connection '{connection_id}' not found",
            )
    logger.info(
        "Deleted LLM provider connection", extra={"connection_id": connection_id}
    )
    return ProviderConnectionResponse(
        id=connection.id,
        display_name=connection.display_name,
        provider=connection.provider,
        base_url=connection.base_url,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        api_key_set=False,
    )
