"""Provider Connection endpoints: connect a vendor once, pick from its models.

A Provider Connection is the persisted record for the "connect a provider once"
flow (OpenHands/OpenHands#15492). The connection stores a *reference* to a named
secret (the API key lives in the SecretsStore), plus the list of models the user
selected from the provider's catalog. The raw key is never returned to clients:
responses carry ``api_key_set`` and the connection's ``secret_name`` is treated
as sensitive metadata.

The key is stored per-connection (not per-provider), so a second key for the same
provider is an additive connection later — multiple-keys-per-provider is
deferred but the data model already supports it.

Endpoints (mounted under ``/api/llm``):

  - GET    /connections                 list connections (masked, no keys)
  - POST   /connections                 create {provider, key, label?, models?}
  - GET    /connections/{id}            connection + its selected models
  - PATCH  /connections/{id}            rotate key / rename label / set models
  - DELETE /connections/{id}            disconnect (+ delete the named secret)
  - POST   /connections/{id}/validate   test the key, return the provider's
                                        model catalog; updates last_validated_at

LLM profiles spawned from a connection store ``api_key = "secret:<secret_name>"``
(see :func:`openhands.agent_server.persistence.llm_secret_ref`) instead of the
raw key, so rotating the key is one SecretsStore write and every referencing
profile picks it up at call time (see ``LLM._get_api_key_value``).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence import (
    ProviderConnection,
    get_connections_store,
    get_secrets_store,
)
from openhands.sdk.llm.utils.unverified_models import (
    _extract_model_and_provider,
    _get_litellm_provider_names,
    get_supported_llm_models,
)
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

connections_router = APIRouter(prefix="/llm/connections", tags=["LLM Connections"])

# Cap on the number of saved connections. Per-connection keys mean a second key
# for the same provider is additive; 64 leaves ample headroom while bounding the
# catalog the GUI renders.
MAX_CONNECTIONS = 64

# Length of the per-connection id (uuid4 hex, 32 chars). Used only for the
# ``secret_name`` derivation below; the id itself is opaque to clients.
_SECRET_NAME_PREFIX = "llm_connection_"


def _connection_secret_name(connection_id: str) -> str:
    """Derive the named-secret key under which a connection's key is stored."""
    return f"{_SECRET_NAME_PREFIX}{connection_id}"


def _now() -> int:
    return int(time.time())


def _provider_names() -> set[str]:
    return _get_litellm_provider_names()


# ── Request / Response models ────────────────────────────────────────────


class ConnectionCreateRequest(BaseModel):
    """Create a connection. ``key`` is written to the SecretsStore; never echoed."""

    provider: str = Field(..., min_length=1, max_length=128)
    key: SecretStr = Field(..., min_length=1)
    label: str | None = Field(default=None, max_length=128)
    models: list[str] = Field(default_factory=list)


class ConnectionUpdateRequest(BaseModel):
    """Partial update a connection.

    ``key`` rotates the named secret (rewrites the SecretsStore entry). ``label``
    and ``models`` are straightforward field updates. At least one field is
    required.
    """

    key: SecretStr | None = None
    label: str | None = None
    models: list[str] | None = None


class ConnectionResponse(BaseModel):
    """Safe connection view — never includes the raw key or the secret value."""

    id: str
    provider: str
    label: str | None = None
    models: list[str] = Field(default_factory=list)
    created_at: int
    last_validated_at: int | None = None
    api_key_set: bool = False


class ValidateResponse(BaseModel):
    """Result of testing a connection's key against the provider's catalog."""

    id: str
    provider: str
    ok: bool
    models: list[str] = Field(default_factory=list)
    error: str | None = None
    validated_at: int


def _to_response(conn: ProviderConnection, *, api_key_set: bool) -> ConnectionResponse:
    return ConnectionResponse(
        id=conn.id,
        provider=conn.provider,
        label=conn.label,
        models=list(conn.models),
        created_at=conn.created_at,
        last_validated_at=conn.last_validated_at,
        api_key_set=api_key_set,
    )


def _api_key_set(secret_name: str) -> bool:
    """True if the named secret backing a connection currently holds a value."""
    store = get_secrets_store()
    value = store.get_secret(secret_name)
    return bool(value and value.strip())


def _get_connection_or_404(connections, connection_id: str) -> ProviderConnection:
    for conn in connections.connections:
        if conn.id == connection_id:
            return conn
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Connection '{connection_id}' not found",
    )


# ── Provider key validation (injectable for tests) ───────────────────────
#
# ``validate_provider_key`` attempts to confirm a key works for a provider and
# returns that provider's model catalog. The default implementation is
# conservative: it never makes a live network call (which would be slow and
# non-deterministic in tests); it returns the static LiteLLM catalog for the
# provider when the key is non-empty, so the wizard can populate the picker.
# A production implementation can override this (or a future flag) to issue a
# real cheap probe and surface a 401/403 cause. The function is module-level so
# tests monkeypatch it.

ValidateFn = Callable[[str, str], tuple[bool, list[str], str | None]]


def validate_provider_key(
    provider: str, key: str
) -> tuple[bool, list[str], str | None]:
    """Default validator: non-empty key => provider's static model catalog.

    Returns ``(ok, models, error)``. ``ok`` is True for a non-empty key against a
    known provider; ``models`` is the provider-filtered LiteLLM catalog; ``error``
    is None on success or a short cause string on failure.
    """
    if not key or not key.strip():
        return False, [], "API key is empty"
    if provider not in _provider_names():
        return False, [], f"Unknown provider '{provider}'"

    all_models = get_supported_llm_models()
    verified_provider_models = set(VERIFIED_MODELS.get(provider, ()))
    filtered: list[str] = []
    for model in all_models:
        model_provider, _, _ = _extract_model_and_provider(model)
        if model_provider == provider or model in verified_provider_models:
            filtered.append(model)
    return True, sorted(set(filtered)), None


# ── Endpoints ────────────────────────────────────────────────────────────


@connections_router.get("", response_model=list[ConnectionResponse])
async def list_connections(request: Request) -> list[ConnectionResponse]:
    """List all saved provider connections (keys never returned)."""
    config = get_config(request)
    store = get_connections_store(config)
    persisted = store.load()
    conns = persisted.connections if persisted is not None else []
    return [
        _to_response(c, api_key_set=_api_key_set(c.secret_name)) for c in conns
    ]


@connections_router.post(
    "",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    request: Request, body: ConnectionCreateRequest
) -> ConnectionResponse:
    """Create a connection: store the key as a named secret, then the record."""
    if body.provider not in _provider_names():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown provider '{body.provider}'",
        )

    config = get_config(request)
    store = get_connections_store(config)
    secrets_store = get_secrets_store(config)

    connection_id = uuid.uuid4().hex
    secret_name = _connection_secret_name(connection_id)

    def add(conn_list):
        if len(conn_list.connections) >= MAX_CONNECTIONS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Connection limit reached ({MAX_CONNECTIONS}). "
                    "Disconnect one before adding a new connection."
                ),
            )
        conn = ProviderConnection(
            id=connection_id,
            provider=body.provider,
            label=body.label,
            secret_name=secret_name,
            models=list(body.models),
            created_at=_now(),
        )
        conn_list.connections.append(conn)
        return conn_list

    try:
        secrets_store.set_secret(
            name=secret_name,
            value=body.key.get_secret_value(),
            description=f"LLM provider connection key for {body.provider}",
        )
    except RuntimeError as e:
        logger.error(f"Connection create blocked (secrets): {e}")
        raise HTTPException(
            status_code=500,
            detail="Secrets file is corrupted or encrypted with a different key",
        )

    try:
        persisted = store.update(add)
    except HTTPException:
        # Roll back the secret we just wrote so we don't leak orphaned keys.
        try:
            secrets_store.delete_secret(secret_name)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            logger.warning(f"Failed to roll back secret {secret_name}")
        raise

    conn = next(c for c in persisted.connections if c.id == connection_id)
    logger.info(
        "Created provider connection",
        extra={"connection_id": connection_id, "provider": body.provider},
    )
    return _to_response(conn, api_key_set=True)


@connections_router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(request: Request, connection_id: str) -> ConnectionResponse:
    """Get a single connection (key never returned)."""
    config = get_config(request)
    store = get_connections_store(config)
    persisted = store.load()
    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found",
        )
    conn = _get_connection_or_404(persisted, connection_id)
    return _to_response(conn, api_key_set=_api_key_set(conn.secret_name))


@connections_router.patch("/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    request: Request, connection_id: str, body: ConnectionUpdateRequest
) -> ConnectionResponse:
    """Update a connection: rotate key, rename label, or set selected models."""
    if body.key is None and body.label is None and body.models is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one of: key, label, models",
        )

    config = get_config(request)
    store = get_connections_store(config)
    secrets_store = get_secrets_store(config)

    if body.key is not None:
        # Rotate the named secret first; the connection record only references it.
        persisted = store.load() or _empty()
        conn_to_rotate = _get_connection_or_404(persisted, connection_id)
        try:
            secrets_store.set_secret(
                name=conn_to_rotate.secret_name,
                value=body.key.get_secret_value(),
                description=(
                    f"LLM provider connection key for {conn_to_rotate.provider}"
                ),
            )
        except RuntimeError as e:
            logger.error(f"Connection rotate blocked (secrets): {e}")
            raise HTTPException(
                status_code=500,
                detail="Secrets file is corrupted or encrypted with a different key",
            )

    def patch(conn_list):
        for c in conn_list.connections:
            if c.id == connection_id:
                if body.label is not None:
                    c = c.model_copy(update={"label": body.label})
                if body.models is not None:
                    c = c.model_copy(update={"models": list(body.models)})
                conn_list.connections = [
                    c if x.id == connection_id else x for x in conn_list.connections
                ]
                return conn_list
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found",
        )

    persisted = store.update(patch)
    conn = next(c for c in persisted.connections if c.id == connection_id)
    return _to_response(conn, api_key_set=_api_key_set(conn.secret_name))


@connections_router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(request: Request, connection_id: str):
    """Disconnect: delete the connection record and its named secret."""
    config = get_config(request)
    store = get_connections_store(config)
    secrets_store = get_secrets_store(config)

    deleted_secret_name: str | None = None

    def remove(conn_list):
        nonlocal deleted_secret_name
        for i, c in enumerate(conn_list.connections):
            if c.id == connection_id:
                deleted_secret_name = c.secret_name
                conn_list.connections.pop(i)
                return conn_list
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found",
        )

    store.update(remove)
    if deleted_secret_name is not None:
        try:
            secrets_store.delete_secret(deleted_secret_name)
        except Exception:  # noqa: BLE001 - record already gone; best-effort
            logger.warning(f"Failed to delete secret {deleted_secret_name}")
    logger.info("Deleted provider connection", extra={"connection_id": connection_id})


@connections_router.post(
    "/{connection_id}/validate", response_model=ValidateResponse
)
async def validate_connection(
    request: Request, connection_id: str
) -> ValidateResponse:
    """Test the connection's key against the provider and return its catalog."""
    config = get_config(request)
    store = get_connections_store(config)
    secrets_store = get_secrets_store(config)

    persisted = store.load() or _empty()
    conn = _get_connection_or_404(persisted, connection_id)
    key = secrets_store.get_secret(conn.secret_name) or ""

    ok, models, error = validate_provider_key(conn.provider, key)
    validated_at = _now()
    if ok:
        # Stamp last_validated_at on success.
        def stamp(conn_list):
            for c in conn_list.connections:
                if c.id == connection_id:
                    c = c.model_copy(update={"last_validated_at": validated_at})
                    conn_list.connections = [
                        c if x.id == connection_id else x
                        for x in conn_list.connections
                    ]
                    return conn_list
            return conn_list

        store.update(stamp)

    return ValidateResponse(
        id=connection_id,
        provider=conn.provider,
        ok=ok,
        models=models,
        error=error,
        validated_at=validated_at,
    )


def _empty():
    """Return a fresh empty PersistedConnections (avoids ``load() or None`` chains)."""
    from openhands.agent_server.persistence import PersistedConnections

    return PersistedConnections()
