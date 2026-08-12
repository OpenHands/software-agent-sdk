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
  - DELETE /connections/{id}            disconnect (+ delete the named secret);
                                        returns the profiles that referenced it
  - POST   /connections/{id}/validate   test the key (catalog-only by default,
                                        live probe with ``?live=true`` or
                                        OH_CONNECTIONS_LIVE_VALIDATE) and return
                                        the model catalog; the response carries
                                        ``verified`` so clients never claim an
                                        unchecked key was authenticated
  - POST   /connections/{id}/profiles   create an LLM profile bound to this
                                        connection's key (api_key by reference)

LLM profiles spawned from a connection store ``api_key = "secret:<secret_name>"``
(see :func:`openhands.agent_server.persistence.llm_secret_ref`) instead of the
raw key, so rotating the key is one SecretsStore write and every referencing
profile picks it up at call time (see ``LLM._get_api_key_value``).
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence import (
    PersistedConnections,
    ProviderConnection,
    get_connections_store,
    get_llm_profile_store,
    get_secrets_store,
    llm_secret_ref,
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
    base_url: str | None = Field(default=None, max_length=2048)
    api_mode: Literal["auto", "chat", "responses"] = "auto"
    custom_headers: dict[str, str] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)


class ConnectionUpdateRequest(BaseModel):
    """Partial update a connection.

    ``key`` rotates the named secret (rewrites the SecretsStore entry).
    ``label``, endpoint settings, and ``models`` are straightforward field
    updates. At least one field is required.
    """

    key: SecretStr | None = None
    label: str | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    api_mode: Literal["auto", "chat", "responses"] | None = None
    custom_headers: dict[str, str] | None = None
    models: list[str] | None = None


class ConnectionResponse(BaseModel):
    """Safe connection view — never includes the raw key or the secret value."""

    id: str
    provider: str
    label: str | None = None
    base_url: str | None = None
    api_mode: Literal["auto", "chat", "responses"] = "auto"
    custom_headers: dict[str, str] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)
    created_at: int
    last_validated_at: int | None = None
    api_key_set: bool = False


class ValidateResponse(BaseModel):
    """Result of testing a connection's key against the provider's catalog.

    ``verified`` distinguishes a real, network-checked key from a catalog-only
    response: it is True only when a live probe confirmed the provider accepted
    the key. Clients must not present the key as authenticated when ``verified``
    is False (the models are the provider's advertised catalog, not proven grants).
    """

    id: str
    provider: str
    ok: bool
    verified: bool = False
    models: list[str] = Field(default_factory=list)
    error: str | None = None
    validated_at: int


class DisconnectResponse(BaseModel):
    """Result of a disconnect: which profiles now reference a missing key."""

    id: str
    affected_profiles: list[str] = Field(default_factory=list)


class CreateProfileFromConnectionRequest(BaseModel):
    """Create an LLM profile that authenticates via this connection's key.

    The profile stores ``api_key = "secret:<connection secret>"`` rather than the
    raw key, so rotating the connection updates every profile at once. ``model``
    must be one of the connection's selected/validated models.
    """

    profile_name: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1)
    # Optional per-profile override. If omitted, the connection endpoint
    # settings are used.
    base_url: str | None = None


class ProfileFromConnectionResponse(BaseModel):
    profile_name: str
    model: str
    provider: str
    connection_id: str


def _to_response(conn: ProviderConnection, *, api_key_set: bool) -> ConnectionResponse:
    api_mode = (
        conn.api_mode if conn.api_mode in {"auto", "chat", "responses"} else "auto"
    )
    return ConnectionResponse(
        id=conn.id,
        provider=conn.provider,
        label=conn.label,
        base_url=conn.base_url,
        api_mode=api_mode,
        custom_headers=dict(conn.custom_headers),
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


def _profiles_referencing(secret_name: str) -> list[str]:
    """Names of LLM profiles whose ``api_key`` points at this connection's secret.

    Used to warn the user before disconnect: these profiles would stop
    authenticating once the named secret is deleted.
    """
    ref = llm_secret_ref(secret_name)
    store = get_llm_profile_store()
    referrers: list[str] = []
    for summary in store.list_summaries():
        name = summary.get("name")
        if not isinstance(name, str):
            continue
        try:
            llm = store.load(name)
        except Exception:  # noqa: BLE001 - skip unreadable profiles
            continue
        api_key = llm.api_key
        raw = api_key.get_secret_value() if isinstance(api_key, SecretStr) else None
        if raw == ref:
            referrers.append(name)
    return sorted(referrers)


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
# ``validate_provider_key`` confirms a key looks usable for a provider and
# returns that provider's model catalog. It reports two distinct things via the
# ``ValidationResult`` fields:
#
#   - ``ok``       the request could proceed (non-empty key, known provider, and
#                  — when a live probe runs — the provider did not reject the key)
#   - ``verified`` whether the key was actually checked against the provider over
#                  the network. When no live probe runs, ``verified`` is False and
#                  the catalog is the provider's *advertised* models, not the ones
#                  the key is proven to grant. Callers/UI must not claim the key
#                  was authenticated when ``verified`` is False.
#
# A live probe is opt-in (``OH_CONNECTIONS_LIVE_VALIDATE=1`` or ``live=True`` on
# the endpoint) because it costs a network round-trip and is not always reachable
# from every deployment. The function is module-level so tests monkeypatch it.

ValidateFn = Callable[..., "ValidationResult"]


class ValidationResult(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    error: str | None = None
    verified: bool = False


def _provider_catalog(provider: str) -> list[str]:
    """Return the provider's advertised model catalog (no network call)."""
    all_models = get_supported_llm_models()
    verified_provider_models = set(VERIFIED_MODELS.get(provider, ()))
    filtered: list[str] = []
    for model in all_models:
        model_provider, _, _ = _extract_model_and_provider(model)
        if model_provider == provider or model in verified_provider_models:
            filtered.append(model)
    return sorted(set(filtered))


def _live_probe(
    provider: str,
    key: str,
    *,
    base_url: str | None = None,
    custom_headers: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """Cheaply check a key against a provider over the network.

    Returns ``(ok, error)``. Uses LiteLLM's provider-endpoint check, which lists
    the provider's models using the supplied key without spending tokens. An
    authentication/permission rejection maps to ``ok=False`` with a short cause;
    connectivity problems are surfaced as an error but do not assert the key is
    invalid.
    """
    import litellm
    from litellm.exceptions import AuthenticationError, PermissionDeniedError

    try:
        litellm.get_valid_models(
            check_provider_endpoint=True,
            custom_llm_provider=provider,
            api_key=key,
            api_base=base_url,
            extra_headers=custom_headers or None,
        )
        return True, None
    except (AuthenticationError, PermissionDeniedError) as e:
        return False, f"Provider rejected the key: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001 - connectivity/other; don't assert invalid
        logger.warning(f"Live validation probe failed for {provider}: {e}")
        return False, f"Could not reach {provider} to verify the key: {str(e)[:200]}"


def validate_provider_key(
    provider: str,
    key: str,
    *,
    live: bool = False,
    base_url: str | None = None,
    custom_headers: dict[str, str] | None = None,
) -> ValidationResult:
    """Validate a provider key and return the models it can select.

    With ``live=False`` (default) this performs input checks only and returns the
    provider's advertised catalog with ``verified=False`` — it does *not* prove
    the key authenticates. With ``live=True`` it additionally issues a cheap
    network probe; on success ``verified`` is True.
    """
    if not key or not key.strip():
        return ValidationResult(ok=False, models=[], error="API key is empty")
    if provider not in _provider_names():
        return ValidationResult(
            ok=False, models=[], error=f"Unknown provider '{provider}'"
        )

    catalog = _provider_catalog(provider)
    if not live:
        return ValidationResult(ok=True, models=catalog, error=None, verified=False)

    ok, error = _live_probe(
        provider,
        key,
        base_url=base_url,
        custom_headers=custom_headers,
    )
    return ValidationResult(
        ok=ok, models=catalog if ok else [], error=error, verified=ok
    )


# ── Endpoints ────────────────────────────────────────────────────────────


@connections_router.get("", response_model=list[ConnectionResponse])
async def list_connections(request: Request) -> list[ConnectionResponse]:
    """List all saved provider connections (keys never returned)."""
    config = get_config(request)
    store = get_connections_store(config)
    persisted = store.load()
    conns = persisted.connections if persisted is not None else []
    return [_to_response(c, api_key_set=_api_key_set(c.secret_name)) for c in conns]


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
            base_url=body.base_url,
            api_mode=body.api_mode,
            custom_headers=dict(body.custom_headers),
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
    if (
        body.key is None
        and body.label is None
        and body.base_url is None
        and body.api_mode is None
        and body.custom_headers is None
        and body.models is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Provide at least one of: key, label, base_url, api_mode, "
                "custom_headers, models"
            ),
        )

    config = get_config(request)
    store = get_connections_store(config)
    secrets_store = get_secrets_store(config)

    def patch(conn_list):
        # The whole update runs under the connections lock. We only rotate the
        # secret *after* confirming the connection still exists, so a concurrent
        # delete can't leave an orphaned rotated key (the earlier version wrote
        # the secret before the record check).
        for c in conn_list.connections:
            if c.id == connection_id:
                if body.key is not None:
                    try:
                        secrets_store.set_secret(
                            name=c.secret_name,
                            value=body.key.get_secret_value(),
                            description=(
                                f"LLM provider connection key for {c.provider}"
                            ),
                        )
                    except RuntimeError as e:
                        logger.error(f"Connection rotate blocked (secrets): {e}")
                        raise HTTPException(
                            status_code=500,
                            detail=(
                                "Secrets file is corrupted or encrypted with a "
                                "different key"
                            ),
                        )
                if body.label is not None:
                    c = c.model_copy(update={"label": body.label})
                if body.base_url is not None:
                    c = c.model_copy(update={"base_url": body.base_url})
                if body.api_mode is not None:
                    c = c.model_copy(update={"api_mode": body.api_mode})
                if body.custom_headers is not None:
                    c = c.model_copy(
                        update={"custom_headers": dict(body.custom_headers)}
                    )
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


@connections_router.delete("/{connection_id}", response_model=DisconnectResponse)
async def delete_connection(request: Request, connection_id: str) -> DisconnectResponse:
    """Disconnect: delete the connection record and its named secret.

    Returns the names of LLM profiles that referenced the connection's key so the
    client can warn that they will stop authenticating until pointed at a new key.
    The profiles are left intact (deleting them silently would be more surprising
    than a clear "these now need a key" message).
    """
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

    affected: list[str] = []
    if deleted_secret_name is None:
        # Peek before mutating so we can report referrers in the response.
        persisted = store.load()
        if persisted is not None:
            for c in persisted.connections:
                if c.id == connection_id:
                    affected = _profiles_referencing(c.secret_name)
                    break

    store.update(remove)
    if deleted_secret_name is not None:
        try:
            secrets_store.delete_secret(deleted_secret_name)
        except Exception:  # noqa: BLE001 - record already gone; best-effort
            logger.warning(f"Failed to delete secret {deleted_secret_name}")
    logger.info(
        "Deleted provider connection",
        extra={"connection_id": connection_id, "affected_profiles": len(affected)},
    )
    return DisconnectResponse(id=connection_id, affected_profiles=affected)


def _live_validation_default() -> bool:
    """Whether validate should probe the provider live unless told otherwise."""
    return os.getenv("OH_CONNECTIONS_LIVE_VALIDATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


@connections_router.post("/{connection_id}/validate", response_model=ValidateResponse)
async def validate_connection(
    request: Request, connection_id: str, live: bool | None = None
) -> ValidateResponse:
    """Test the connection's key against the provider and return its catalog.

    When ``live`` is true (or ``OH_CONNECTIONS_LIVE_VALIDATE`` is set) the key is
    probed against the provider over the network and ``verified`` reflects the
    real result. Otherwise the response is catalog-only with ``verified=false``.
    On a successful validation the connection's ``last_validated_at`` is stamped
    and its ``models`` are set to the returned catalog so a profile can be spawned
    from them without a second call.
    """
    config = get_config(request)
    store = get_connections_store(config)
    secrets_store = get_secrets_store(config)

    persisted = store.load() or _empty()
    conn = _get_connection_or_404(persisted, connection_id)
    key = secrets_store.get_secret(conn.secret_name) or ""

    do_live = _live_validation_default() if live is None else live
    result = validate_provider_key(
        conn.provider,
        key,
        live=do_live,
        base_url=conn.base_url,
        custom_headers=conn.custom_headers,
    )
    validated_at = _now()
    if result.ok:
        # Persist the catalog + timestamp so profile creation can reuse them.
        def stamp(conn_list):
            for c in conn_list.connections:
                if c.id == connection_id:
                    c = c.model_copy(
                        update={
                            "last_validated_at": validated_at,
                            "models": list(result.models),
                        }
                    )
                    conn_list.connections = [
                        c if x.id == connection_id else x for x in conn_list.connections
                    ]
                    return conn_list
            return conn_list

        store.update(stamp)

    return ValidateResponse(
        id=connection_id,
        provider=conn.provider,
        ok=result.ok,
        verified=result.verified,
        models=result.models,
        error=result.error,
        validated_at=validated_at,
    )


@connections_router.post(
    "/{connection_id}/profiles",
    response_model=ProfileFromConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile_from_connection(
    request: Request,
    connection_id: str,
    body: CreateProfileFromConnectionRequest,
) -> ProfileFromConnectionResponse:
    """Create an LLM profile backed by this connection's key.

    This is the "pick from every model the connection offers" step: it saves a
    named LLM profile whose ``api_key`` is a ``secret:<name>`` reference to the
    connection's stored key, so the profile authenticates without duplicating the
    key and follows the key when it is rotated. ``model`` must be one of the
    connection's selected/validated models.
    """
    from openhands.sdk.llm import LLM
    from openhands.sdk.llm.llm_profile_store import (
        PROFILE_NAME_REGEX,
        ProfileLimitExceeded,
    )

    config = get_config(request)
    store = get_connections_store(config)

    persisted = store.load() or _empty()
    conn = _get_connection_or_404(persisted, connection_id)

    if not PROFILE_NAME_REGEX.match(body.profile_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid profile name '{body.profile_name}'",
        )
    if conn.models and body.model not in conn.models:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Model '{body.model}' is not one of the connection's selected "
                "models. Validate the connection or choose a listed model."
            ),
        )

    api_mode = (
        conn.api_mode if conn.api_mode in {"auto", "chat", "responses"} else "auto"
    )
    llm = LLM(
        model=body.model,
        base_url=body.base_url or conn.base_url,
        api_mode=api_mode,
        extra_headers=dict(conn.custom_headers) or None,
        api_key=SecretStr(llm_secret_ref(conn.secret_name)),
        usage_id=body.profile_name,
    )

    profile_store = get_llm_profile_store()
    from openhands.agent_server.profiles_router import MAX_PROFILES

    try:
        profile_store.save(
            body.profile_name,
            llm,
            include_secrets=True,
            max_profiles=MAX_PROFILES,
        )
    except ProfileLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Profile limit reached ({MAX_PROFILES}). "
                "Delete a profile before creating a new one."
            ),
        )

    logger.info(
        "Created profile from connection",
        extra={
            "connection_id": connection_id,
            "profile_name": body.profile_name,
            "model": body.model,
        },
    )
    return ProfileFromConnectionResponse(
        profile_name=body.profile_name,
        model=body.model,
        provider=conn.provider,
        connection_id=connection_id,
    )


def _empty() -> PersistedConnections:
    """Return a fresh empty PersistedConnections (avoids ``load() or None`` chains)."""
    return PersistedConnections()
