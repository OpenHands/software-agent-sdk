"""Model provider endpoints: connect a provider once, manage its models under it.

A "model provider" is the persisted record for the provider-centric flow in
OpenHands/OpenHands#15492. One API key is held on the provider and shared by
every model nested under it; the user manages those models (add / edit / remove)
directly on the provider. The key is stored as a *named secret* in the
SecretsStore — the provider record keeps only ``secret_name`` and the raw key is
never returned (responses carry ``api_key_set`` instead).

Endpoints (mounted under ``/api/llm``). The list of *available provider kinds*
for the "add provider" preset picker stays at ``GET /api/llm/providers``
(see ``llm_router``); these configured-provider records live under
``/api/llm/model-providers`` to avoid shadowing it:

  - GET    /model-providers                     list providers (masked, no keys)
  - POST   /model-providers                     create {display_name, kind,
                                                base_url, wire_api, key,
                                                custom_headers, models?}
  - GET    /model-providers/{id}                a single provider (masked)
  - PATCH  /model-providers/{id}                update fields / rotate key
  - DELETE /model-providers/{id}                remove provider + its named secret
  - POST   /model-providers/{id}/models         add a nested model {name, wire_api?}
  - PATCH  /model-providers/{id}/models/{name}  edit a nested model
  - DELETE /model-providers/{id}/models/{name}  remove a nested model
  - POST   /model-providers/{id}/test           optional key probe; NEVER mutates
                                                the curated model list
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence import (
    ModelProvider,
    PersistedProviders,
    ProviderModel,
    get_providers_store,
    get_secrets_store,
)
from openhands.agent_server.persistence.models import WireApi
from openhands.sdk.llm.utils.unverified_models import (
    _extract_model_and_provider,
    _get_litellm_provider_names,
    get_supported_llm_models,
)
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

providers_router = APIRouter(prefix="/llm/model-providers", tags=["Model Providers"])

# Cap on saved providers. Generous headroom while bounding the config document.
MAX_PROVIDERS = 64
# Cap on models nested under a single provider.
MAX_MODELS_PER_PROVIDER = 256

_SECRET_NAME_PREFIX = "llm_provider_"


def _secret_name(provider_id: str) -> str:
    return f"{_SECRET_NAME_PREFIX}{provider_id}"


def _now() -> int:
    return int(time.time())


# ── Request / Response models ────────────────────────────────────────────


class ProviderModelPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    wire_api: WireApi | None = None


class ProviderCreateRequest(BaseModel):
    """Create a provider. ``key`` is written to the SecretsStore; never echoed."""

    display_name: str = Field(..., min_length=1, max_length=128)
    kind: str = Field(default="custom", max_length=128)
    key: SecretStr = Field(..., min_length=1)
    base_url: str | None = Field(default=None, max_length=2048)
    wire_api: WireApi = "auto"
    custom_headers: dict[str, str] = Field(default_factory=dict)
    models: list[ProviderModelPayload] = Field(default_factory=list)


class ProviderUpdateRequest(BaseModel):
    """Partial update. ``key`` rotates the named secret. At least one field."""

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: str | None = Field(default=None, max_length=128)
    key: SecretStr | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    wire_api: WireApi | None = None
    custom_headers: dict[str, str] | None = None


class ModelResponse(BaseModel):
    name: str
    wire_api: WireApi | None = None


class ProviderResponse(BaseModel):
    """Safe provider view — never includes the raw key or the secret name."""

    id: str
    display_name: str
    kind: str
    base_url: str | None = None
    wire_api: WireApi = "auto"
    custom_headers: dict[str, str] = Field(default_factory=dict)
    models: list[ModelResponse] = Field(default_factory=list)
    created_at: int
    updated_at: int
    api_key_set: bool = False


class TestResponse(BaseModel):
    """Result of probing a provider's key. Never mutates the model list.

    ``verified`` is True only when a live network probe confirmed the provider
    accepted the key. ``suggested_models`` is the provider's advertised catalog,
    offered purely as a convenience for populating model rows.
    """

    id: str
    ok: bool
    verified: bool = False
    suggested_models: list[str] = Field(default_factory=list)
    error: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _to_response(p: ModelProvider, *, api_key_set: bool) -> ProviderResponse:
    return ProviderResponse(
        id=p.id,
        display_name=p.display_name,
        kind=p.kind,
        base_url=p.base_url,
        wire_api=p.wire_api,
        custom_headers=dict(p.custom_headers),
        models=[ModelResponse(name=m.name, wire_api=m.wire_api) for m in p.models],
        created_at=p.created_at,
        updated_at=p.updated_at,
        api_key_set=api_key_set,
    )


def _api_key_set(secret_name: str) -> bool:
    value = get_secrets_store().get_secret(secret_name)
    return bool(value and value.strip())


def _get_provider_or_404(
    persisted: PersistedProviders | None, provider_id: str
) -> ModelProvider:
    for p in persisted.providers if persisted else []:
        if p.id == provider_id:
            return p
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Provider '{provider_id}' not found",
    )


def _provider_catalog(kind: str) -> list[str]:
    """Provider's advertised model catalog (no network call), for suggestions."""
    all_models = get_supported_llm_models()
    verified = set(VERIFIED_MODELS.get(kind, ()))
    out: list[str] = []
    for model in all_models:
        model_provider, _, _ = _extract_model_and_provider(model)
        if model_provider == kind or model in verified:
            prefix = f"{kind}/"
            out.append(model[len(prefix) :] if model.startswith(prefix) else model)
    out.extend(verified)
    return sorted(set(out))


def _live_probe(
    kind: str,
    key: str,
    *,
    base_url: str | None,
) -> tuple[bool, str | None]:
    """Cheaply check a key against a provider over the network.

    Uses LiteLLM's provider-endpoint listing, which validates the key without
    spending tokens. Auth/permission rejections map to ``(False, cause)``;
    connectivity failures are surfaced as an error but do not assert invalidity.
    """
    import litellm
    from litellm.exceptions import AuthenticationError, PermissionDeniedError

    try:
        litellm.get_valid_models(
            check_provider_endpoint=True,
            custom_llm_provider=kind,
            api_key=key,
            api_base=base_url,
        )
        return True, None
    except (AuthenticationError, PermissionDeniedError) as e:
        return False, f"Provider rejected the key: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001 - connectivity/other; don't assert invalid
        logger.warning(f"Live probe failed for {kind}: {e}")
        return False, f"Could not reach {kind} to verify the key: {str(e)[:200]}"


# ── Provider endpoints ───────────────────────────────────────────────────


@providers_router.get("", response_model=list[ProviderResponse])
async def list_providers(request: Request) -> list[ProviderResponse]:
    """List all saved model providers (keys never returned)."""
    store = get_providers_store(get_config(request))
    persisted = store.load()
    providers = persisted.providers if persisted else []
    return [_to_response(p, api_key_set=_api_key_set(p.secret_name)) for p in providers]


@providers_router.post(
    "", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED
)
async def create_provider(
    request: Request, body: ProviderCreateRequest
) -> ProviderResponse:
    """Create a provider: store the key as a named secret, then the record."""
    config = get_config(request)
    store = get_providers_store(config)
    secrets_store = get_secrets_store(config)

    provider_id = uuid.uuid4().hex
    secret_name = _secret_name(provider_id)
    now = _now()

    def add(persisted: PersistedProviders) -> PersistedProviders:
        if len(persisted.providers) >= MAX_PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Provider limit reached ({MAX_PROVIDERS}). "
                    "Remove one before adding a new provider."
                ),
            )
        if len(body.models) > MAX_MODELS_PER_PROVIDER:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Too many models (max {MAX_MODELS_PER_PROVIDER})",
            )
        persisted.providers.append(
            ModelProvider(
                id=provider_id,
                display_name=body.display_name,
                kind=body.kind,
                base_url=body.base_url,
                wire_api=body.wire_api,
                custom_headers=dict(body.custom_headers),
                secret_name=secret_name,
                models=[
                    ProviderModel(name=m.name, wire_api=m.wire_api) for m in body.models
                ],
                created_at=now,
                updated_at=now,
            )
        )
        return persisted

    try:
        secrets_store.set_secret(
            name=secret_name,
            value=body.key.get_secret_value(),
            description=f"LLM provider key for {body.display_name}",
        )
    except RuntimeError as e:
        logger.error(f"Provider create blocked (secrets): {e}")
        raise HTTPException(
            status_code=500,
            detail="Secrets file is corrupted or encrypted with a different key",
        )

    try:
        persisted = store.update(add)
    except HTTPException:
        # Roll back the secret we just wrote so we don't leak an orphaned key.
        try:
            secrets_store.delete_secret(secret_name)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            logger.warning(f"Failed to roll back secret {secret_name}")
        raise

    provider = next(p for p in persisted.providers if p.id == provider_id)
    logger.info("Created model provider", extra={"provider_id": provider_id})
    return _to_response(provider, api_key_set=True)


@providers_router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(request: Request, provider_id: str) -> ProviderResponse:
    """Get a single provider (key never returned)."""
    store = get_providers_store(get_config(request))
    provider = _get_provider_or_404(store.load(), provider_id)
    return _to_response(provider, api_key_set=_api_key_set(provider.secret_name))


@providers_router.patch("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    request: Request, provider_id: str, body: ProviderUpdateRequest
) -> ProviderResponse:
    """Update provider fields or rotate its key. Models are managed separately."""
    if all(
        v is None
        for v in (
            body.display_name,
            body.kind,
            body.key,
            body.base_url,
            body.wire_api,
            body.custom_headers,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Provide at least one of: display_name, kind, key, base_url, "
                "wire_api, custom_headers"
            ),
        )

    config = get_config(request)
    store = get_providers_store(config)
    secrets_store = get_secrets_store(config)

    def patch(persisted: PersistedProviders) -> PersistedProviders:
        # Runs under the providers lock; rotate the secret only after confirming
        # the provider still exists so a concurrent delete can't orphan a key.
        for p in persisted.providers:
            if p.id == provider_id:
                if body.key is not None:
                    try:
                        secrets_store.set_secret(
                            name=p.secret_name,
                            value=body.key.get_secret_value(),
                            description=f"LLM provider key for {p.display_name}",
                        )
                    except RuntimeError as e:
                        logger.error(f"Provider rotate blocked (secrets): {e}")
                        raise HTTPException(
                            status_code=500,
                            detail=(
                                "Secrets file is corrupted or encrypted with a "
                                "different key"
                            ),
                        )
                updates: dict = {"updated_at": _now()}
                if body.display_name is not None:
                    updates["display_name"] = body.display_name
                if body.kind is not None:
                    updates["kind"] = body.kind
                if body.base_url is not None:
                    updates["base_url"] = body.base_url
                if body.wire_api is not None:
                    updates["wire_api"] = body.wire_api
                if body.custom_headers is not None:
                    updates["custom_headers"] = dict(body.custom_headers)
                updated = p.model_copy(update=updates)
                persisted.providers = [
                    updated if x.id == provider_id else x for x in persisted.providers
                ]
                return persisted
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_id}' not found",
        )

    persisted = store.update(patch)
    provider = next(p for p in persisted.providers if p.id == provider_id)
    return _to_response(provider, api_key_set=_api_key_set(provider.secret_name))


@providers_router.delete(
    "/{provider_id}", status_code=status.HTTP_200_OK, response_model=ProviderResponse
)
async def delete_provider(request: Request, provider_id: str) -> ProviderResponse:
    """Remove a provider and its named secret."""
    config = get_config(request)
    store = get_providers_store(config)
    secrets_store = get_secrets_store(config)

    removed: dict[str, ModelProvider] = {}

    def remove(persisted: PersistedProviders) -> PersistedProviders:
        for i, p in enumerate(persisted.providers):
            if p.id == provider_id:
                removed["p"] = persisted.providers.pop(i)
                return persisted
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_id}' not found",
        )

    store.update(remove)
    provider = removed["p"]
    try:
        secrets_store.delete_secret(provider.secret_name)
    except Exception:  # noqa: BLE001 - record already gone; best-effort
        logger.warning(f"Failed to delete secret {provider.secret_name}")
    logger.info("Deleted model provider", extra={"provider_id": provider_id})
    return _to_response(provider, api_key_set=False)


# ── Nested model endpoints ───────────────────────────────────────────────


@providers_router.post(
    "/{provider_id}/models",
    response_model=ProviderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_model(
    request: Request, provider_id: str, body: ProviderModelPayload
) -> ProviderResponse:
    """Add a model under the provider (shares the provider's key/endpoint)."""
    store = get_providers_store(get_config(request))

    def mutate(persisted: PersistedProviders) -> PersistedProviders:
        for p in persisted.providers:
            if p.id == provider_id:
                if any(m.name == body.name for m in p.models):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Model '{body.name}' already exists",
                    )
                if len(p.models) >= MAX_MODELS_PER_PROVIDER:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Model limit reached ({MAX_MODELS_PER_PROVIDER})",
                    )
                models = [
                    *p.models,
                    ProviderModel(name=body.name, wire_api=body.wire_api),
                ]
                updated = p.model_copy(update={"models": models, "updated_at": _now()})
                persisted.providers = [
                    updated if x.id == provider_id else x for x in persisted.providers
                ]
                return persisted
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_id}' not found",
        )

    persisted = store.update(mutate)
    provider = next(p for p in persisted.providers if p.id == provider_id)
    return _to_response(provider, api_key_set=_api_key_set(provider.secret_name))


@providers_router.patch(
    "/{provider_id}/models/{model_name}", response_model=ProviderResponse
)
async def update_model(
    request: Request,
    provider_id: str,
    model_name: str,
    body: ProviderModelPayload,
) -> ProviderResponse:
    """Rename a model and/or change its per-model wire-API override."""
    store = get_providers_store(get_config(request))

    def mutate(persisted: PersistedProviders) -> PersistedProviders:
        for p in persisted.providers:
            if p.id == provider_id:
                if not any(m.name == model_name for m in p.models):
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Model '{model_name}' not found",
                    )
                if body.name != model_name and any(
                    m.name == body.name for m in p.models
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Model '{body.name}' already exists",
                    )
                models = [
                    ProviderModel(name=body.name, wire_api=body.wire_api)
                    if m.name == model_name
                    else m
                    for m in p.models
                ]
                updated = p.model_copy(update={"models": models, "updated_at": _now()})
                persisted.providers = [
                    updated if x.id == provider_id else x for x in persisted.providers
                ]
                return persisted
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_id}' not found",
        )

    persisted = store.update(mutate)
    provider = next(p for p in persisted.providers if p.id == provider_id)
    return _to_response(provider, api_key_set=_api_key_set(provider.secret_name))


@providers_router.delete(
    "/{provider_id}/models/{model_name}", response_model=ProviderResponse
)
async def remove_model(
    request: Request, provider_id: str, model_name: str
) -> ProviderResponse:
    """Remove a model from the provider."""
    store = get_providers_store(get_config(request))

    def mutate(persisted: PersistedProviders) -> PersistedProviders:
        for p in persisted.providers:
            if p.id == provider_id:
                if not any(m.name == model_name for m in p.models):
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Model '{model_name}' not found",
                    )
                models = [m for m in p.models if m.name != model_name]
                updated = p.model_copy(update={"models": models, "updated_at": _now()})
                persisted.providers = [
                    updated if x.id == provider_id else x for x in persisted.providers
                ]
                return persisted
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_id}' not found",
        )

    persisted = store.update(mutate)
    provider = next(p for p in persisted.providers if p.id == provider_id)
    return _to_response(provider, api_key_set=_api_key_set(provider.secret_name))


# ── Optional test probe ──────────────────────────────────────────────────


@providers_router.post("/{provider_id}/test", response_model=TestResponse)
async def test_provider(request: Request, provider_id: str) -> TestResponse:
    """Probe the provider's stored key and suggest catalog models.

    This never mutates the provider's curated model list — ``suggested_models``
    is offered only as a convenience for the "add model" affordance.
    """
    config = get_config(request)
    store = get_providers_store(config)
    secrets_store = get_secrets_store(config)

    provider = _get_provider_or_404(store.load(), provider_id)
    key = secrets_store.get_secret(provider.secret_name) or ""
    if not key.strip():
        return TestResponse(id=provider_id, ok=False, error="No API key stored")

    suggested = _provider_catalog(provider.kind)
    if provider.kind not in _get_litellm_provider_names():
        # Unknown/custom endpoint: can't probe, only offer the catalog.
        return TestResponse(
            id=provider_id, ok=True, verified=False, suggested_models=suggested
        )

    ok, error = _live_probe(provider.kind, key, base_url=provider.base_url)
    return TestResponse(
        id=provider_id,
        ok=ok,
        verified=ok,
        suggested_models=suggested if ok else [],
        error=error,
    )
