from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from openhands.agent_server.persistence import (
    CustomSecretCreate,
    CustomSecretResponse,
    PersistedSettings,
    SecretsResponse,
    get_secrets_store,
    get_settings_store,
)
from openhands.sdk.settings import (
    ConversationSettings,
    SettingsSchema,
    export_agent_settings_schema,
)


settings_router = APIRouter(prefix="/settings", tags=["Settings"])


# ── Schema Endpoints ─────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_agent_settings_schema() -> SettingsSchema:
    # ``AgentSettings`` is now a discriminated union over
    # ``OpenHandsAgentSettings`` and ``ACPAgentSettings``; the combined
    # schema tags sections with a ``variant`` so the frontend can
    # show LLM-only or ACP-only sections based on the active
    # ``agent_kind`` value.
    return export_agent_settings_schema()


@lru_cache(maxsize=1)
def _get_conversation_settings_schema() -> SettingsSchema:
    return ConversationSettings.export_schema()


@settings_router.get("/agent-schema", response_model=SettingsSchema)
async def get_agent_settings_schema() -> SettingsSchema:
    """Return the schema used to render AgentSettings-based settings forms."""
    return _get_agent_settings_schema()


@settings_router.get("/conversation-schema", response_model=SettingsSchema)
async def get_conversation_settings_schema() -> SettingsSchema:
    """Return the schema used to render ConversationSettings-based forms."""
    return _get_conversation_settings_schema()


# ── Settings CRUD Endpoints ──────────────────────────────────────────────


def _get_config(request: Request):
    """Get config from app state."""
    return getattr(request.app.state, "config", None)


class SettingsResponse(BaseModel):
    """Response model for settings."""

    agent_settings: dict[str, Any]
    conversation_settings: dict[str, Any]
    llm_api_key_is_set: bool


class SettingsUpdateRequest(BaseModel):
    """Request model for updating settings."""

    agent_settings_diff: dict[str, Any] | None = None
    conversation_settings_diff: dict[str, Any] | None = None


@settings_router.get("", response_model=SettingsResponse)
async def get_settings(
    request: Request,
    expose_secrets: bool = False,
) -> SettingsResponse:
    """Get current settings.

    Returns the persisted settings including agent configuration,
    conversation settings, and whether an LLM API key is configured.

    Args:
        expose_secrets: If True, return actual secret values instead of masked
            placeholders. Use this for internal automation scripts that need
            LLM API keys. Default: False (secrets are masked as "**********").
    """
    config = _get_config(request)
    store = get_settings_store(config)
    settings = store.load() or PersistedSettings()

    # Build serialization context based on expose_secrets flag
    context = {"expose_secrets": True} if expose_secrets else {}

    return SettingsResponse(
        agent_settings=settings.agent_settings.model_dump(mode="json", context=context),
        conversation_settings=settings.conversation_settings.model_dump(mode="json"),
        llm_api_key_is_set=settings.llm_api_key_is_set,
    )


@settings_router.post("", response_model=SettingsResponse)
async def update_settings(
    request: Request, payload: SettingsUpdateRequest
) -> SettingsResponse:
    """Update settings with partial changes.

    Accepts ``agent_settings_diff`` and/or ``conversation_settings_diff``
    for incremental updates. Values are deep-merged with existing settings.
    """
    config = _get_config(request)
    store = get_settings_store(config)
    settings = store.load() or PersistedSettings()

    # Apply updates
    update_data = payload.model_dump(exclude_none=True)
    if update_data:
        settings.update(update_data)
        store.save(settings)

    return SettingsResponse(
        agent_settings=settings.agent_settings.model_dump(mode="json"),
        conversation_settings=settings.conversation_settings.model_dump(mode="json"),
        llm_api_key_is_set=settings.llm_api_key_is_set,
    )


# ── Secrets CRUD Endpoints ───────────────────────────────────────────────


@settings_router.get("/secrets", response_model=SecretsResponse)
async def list_secrets(request: Request) -> SecretsResponse:
    """List all available secrets (names and descriptions only, no values)."""
    config = _get_config(request)
    store = get_secrets_store(config)
    secrets = store.load()

    if secrets is None:
        return SecretsResponse(secrets=[])

    return SecretsResponse(
        secrets=[
            CustomSecretResponse(name=name, description=secret.description)
            for name, secret in secrets.custom_secrets.items()
        ]
    )


@settings_router.get("/secrets/{name}")
async def get_secret_value(request: Request, name: str) -> Response:
    """Get a single secret value by name.

    Returns the raw secret value as plain text. This endpoint is designed
    to be used with LookupSecret for lazy secret resolution.
    """
    config = _get_config(request)
    store = get_secrets_store(config)
    value = store.get_secret(name)

    if value is None:
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found")

    return Response(content=value, media_type="text/plain")


@settings_router.post("/secrets", response_model=CustomSecretResponse)
async def create_secret(
    request: Request, secret: CustomSecretCreate
) -> CustomSecretResponse:
    """Create or update a custom secret."""
    config = _get_config(request)
    store = get_secrets_store(config)

    store.set_secret(
        name=secret.name,
        value=secret.value.get_secret_value(),
        description=secret.description,
    )

    return CustomSecretResponse(name=secret.name, description=secret.description)


@settings_router.delete("/secrets/{name}")
async def delete_secret(request: Request, name: str) -> dict[str, bool]:
    """Delete a custom secret by name."""
    config = _get_config(request)
    store = get_secrets_store(config)

    deleted = store.delete_secret(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found")

    return {"deleted": True}
