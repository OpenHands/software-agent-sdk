import re
from functools import lru_cache
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ValidationError

from openhands.agent_server.persistence import (
    CustomSecretCreate,
    CustomSecretResponse,
    PersistedSettings,
    SecretsResponse,
    get_secrets_store,
    get_settings_store,
)
from openhands.agent_server.persistence.models import SettingsUpdatePayload
from openhands.sdk.logger import get_logger
from openhands.sdk.settings import (
    ConversationSettings,
    SettingsSchema,
    export_agent_settings_schema,
)


logger = get_logger(__name__)

# ── Route Path Constants ─────────────────────────────────────────────────
# These are relative to the router prefix (/settings).
# When mounted on /api, full paths become /api/settings, /api/settings/secrets, etc.
# Note: RemoteWorkspace (client) uses absolute paths (e.g., "/api/settings")
# while this router uses relative paths. The paths are intentionally separate
# to match their respective contexts (router prefix vs full URL path).
SETTINGS_PATH = ""  # -> /api/settings
SECRETS_PATH = "/secrets"  # -> /api/settings/secrets
SECRET_VALUE_PATH = "/secrets/{name}"  # -> /api/settings/secrets/{name}

settings_router = APIRouter(prefix="/settings", tags=["Settings"])

# Validation pattern for secret names
_SECRET_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


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
    """Get config from app state.

    Raises:
        HTTPException: 503 if config is not initialized.
    """
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=503, detail="Server not fully initialized")
    return config


def _validate_secret_name(name: str) -> None:
    """Validate secret name format.

    Secret names must:
    - Start with a letter
    - Contain only letters, numbers, and underscores
    - Be 1-64 characters long

    Raises:
        HTTPException: 422 if name format is invalid.
    """
    if not _SECRET_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Invalid secret name format. Must start with a letter, "
                "contain only letters, numbers, and underscores, "
                "and be 1-64 characters long."
            ),
        )


class SettingsResponse(BaseModel):
    """Response model for settings."""

    agent_settings: dict[str, Any]
    conversation_settings: dict[str, Any]
    llm_api_key_is_set: bool


class SettingsUpdateRequest(BaseModel):
    """Request model for updating settings."""

    agent_settings_diff: dict[str, Any] | None = None
    conversation_settings_diff: dict[str, Any] | None = None


@settings_router.get(SETTINGS_PATH, response_model=SettingsResponse)
async def get_settings(
    request: Request,
    expose_secrets: bool | None = None,  # noqa: ARG001 - checked via query_params
) -> SettingsResponse:
    """Get current settings.

    Returns the persisted settings including agent configuration,
    conversation settings, and whether an LLM API key is configured.

    Args:
        expose_secrets: REJECTED - use X-Expose-Secrets header instead.
            Query parameters appear in URLs which are logged by proxies,
            stored in browser history, and cached by intermediate systems.
            Use the ``X-Expose-Secrets: true`` header for secure secret exposure.

    Note:
        **Security**: The ``X-Expose-Secrets: true`` header is the only
        supported method for exposing secrets. Query parameter usage is
        rejected to prevent secrets from appearing in access logs.
    """
    # Check header for expose_secrets (the only supported method)
    expose_via_header = request.headers.get("X-Expose-Secrets", "").lower() == "true"

    # Reject ANY query parameter usage for security - URLs are logged by proxies.
    # Check if 'expose_secrets' appears in query params regardless of value
    # (expose_secrets=false or expose_secrets=0 should also be rejected).
    if "expose_secrets" in request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Query parameter 'expose_secrets' is rejected for security reasons. "
                "Use header 'X-Expose-Secrets: true' instead."
            ),
        )

    should_expose = expose_via_header

    config = _get_config(request)
    store = get_settings_store(config)
    settings = store.load() or PersistedSettings()

    # Audit log when secrets are exposed (always log if expose requested)
    if should_expose:
        client_host = request.client.host if request.client else "unknown"
        logger.info(
            "Secrets exposed via settings API",
            extra={
                "client_host": client_host,
                "expose_via_header": expose_via_header,
                "has_llm_api_key": settings.llm_api_key_is_set,
            },
        )

    # Build serialization context based on expose_secrets flag
    context = {"expose_secrets": True} if should_expose else {}

    return SettingsResponse(
        agent_settings=settings.agent_settings.model_dump(mode="json", context=context),
        conversation_settings=settings.conversation_settings.model_dump(mode="json"),
        llm_api_key_is_set=settings.llm_api_key_is_set,
    )


@settings_router.patch(SETTINGS_PATH, response_model=SettingsResponse)
async def update_settings(
    request: Request, payload: SettingsUpdateRequest
) -> SettingsResponse:
    """Update settings with partial changes.

    Accepts ``agent_settings_diff`` and/or ``conversation_settings_diff``
    for incremental updates. Values are deep-merged with existing settings.

    Uses file locking to prevent concurrent updates from overwriting each other.

    Raises:
        HTTPException: 400 if the update payload contains invalid values.
    """
    config = _get_config(request)
    store = get_settings_store(config)

    update_data = payload.model_dump(exclude_none=True)
    if not update_data:
        # No updates provided - this is a client error
        raise HTTPException(
            status_code=400,
            detail=(
                "At least one of agent_settings_diff or "
                "conversation_settings_diff must be provided"
            ),
        )

    # Apply updates atomically with file locking
    def apply_update(settings: PersistedSettings) -> PersistedSettings:
        settings.update(cast(SettingsUpdatePayload, update_data))
        return settings

    client_host = request.client.host if request.client else "unknown"
    try:
        settings = store.update(apply_update)
        # Audit log: settings modified
        logger.info(
            "Settings updated",
            extra={
                "client_host": client_host,
                "agent_settings_modified": "agent_settings_diff" in update_data,
                "conversation_settings_modified": (
                    "conversation_settings_diff" in update_data
                ),
            },
        )
    except ValidationError as e:
        # Audit log: validation failed
        logger.warning(
            "Settings update validation failed",
            extra={"client_host": client_host},
        )
        # 422 Unprocessable Entity - semantic validation failure
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    except (OSError, PermissionError):
        logger.error("Settings update failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update settings")

    # Don't expose secrets in PATCH response (consistent with GET behavior)
    return SettingsResponse(
        agent_settings=settings.agent_settings.model_dump(mode="json"),
        conversation_settings=settings.conversation_settings.model_dump(mode="json"),
        llm_api_key_is_set=settings.llm_api_key_is_set,
    )


# ── Secrets CRUD Endpoints ───────────────────────────────────────────────
# TODO: Consider adding rate limiting to secret endpoints to prevent:
# 1. Brute-force enumeration of secret names
# 2. DoS via repeated file operations
# 3. Timing attacks to determine existence
# See: https://github.com/laurentS/slowapi for FastAPI rate limiting


@settings_router.get(SECRETS_PATH, response_model=SecretsResponse)
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


@settings_router.get(SECRET_VALUE_PATH)
async def get_secret_value(request: Request, name: str) -> Response:
    """Get a single secret value by name.

    Returns the raw secret value as plain text. This endpoint is designed
    to be used with LookupSecret for lazy secret resolution.

    Raises:
        HTTPException: 400 if name format is invalid, 404 if secret not found.
    """
    _validate_secret_name(name)

    config = _get_config(request)
    store = get_secrets_store(config)
    value = store.get_secret(name)

    if value is None:
        # Use generic message to prevent secret name enumeration attacks
        raise HTTPException(status_code=404, detail="Secret not found")

    logger.info(
        "Secret accessed",
        extra={
            "secret_name": name,
            "client_host": request.client.host if request.client else "unknown",
        },
    )
    return Response(content=value, media_type="text/plain")


@settings_router.put(SECRETS_PATH, response_model=CustomSecretResponse)
async def create_secret(
    request: Request, secret: CustomSecretCreate
) -> CustomSecretResponse:
    """Create or update a custom secret (upsert).

    Raises:
        HTTPException: 400 if secret name format is invalid.
    """
    _validate_secret_name(secret.name)

    config = _get_config(request)
    store = get_secrets_store(config)

    try:
        store.set_secret(
            name=secret.name,
            value=secret.value.get_secret_value(),
            description=secret.description,
        )
    except (OSError, PermissionError):
        logger.error("Failed to save secret", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save secret")

    logger.info(
        "Secret created/updated",
        extra={
            "secret_name": secret.name,
            "client_host": request.client.host if request.client else "unknown",
        },
    )
    return CustomSecretResponse(name=secret.name, description=secret.description)


@settings_router.delete(SECRET_VALUE_PATH)
async def delete_secret(request: Request, name: str) -> dict[str, bool]:
    """Delete a custom secret by name.

    Raises:
        HTTPException: 400 if name format is invalid, 404 if secret not found.
    """
    _validate_secret_name(name)

    config = _get_config(request)
    store = get_secrets_store(config)

    deleted = store.delete_secret(name)
    if not deleted:
        # Use generic message to prevent secret name enumeration attacks
        raise HTTPException(status_code=404, detail="Secret not found")

    logger.info(
        "Secret deleted",
        extra={
            "secret_name": name,
            "client_host": request.client.host if request.client else "unknown",
        },
    )
    return {"deleted": True}
