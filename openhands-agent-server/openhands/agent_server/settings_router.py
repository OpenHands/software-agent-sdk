import re
from functools import lru_cache
from typing import Any, Literal, cast

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

# Valid values for X-Expose-Secrets header
ExposeSecretsMode = Literal["encrypted", "plaintext"]


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


def _parse_expose_secrets_header(request: Request) -> ExposeSecretsMode | None:
    """Parse X-Expose-Secrets header value.

    Returns:
        "encrypted", "plaintext", or None (if header not present or invalid).

    Raises:
        HTTPException: 400 if header has invalid value.
    """
    header_value = request.headers.get("X-Expose-Secrets", "").lower().strip()

    if not header_value:
        return None

    # Legacy "true" value - treat as "encrypted" for safety
    if header_value == "true":
        return "encrypted"

    if header_value in ("encrypted", "plaintext"):
        return cast(ExposeSecretsMode, header_value)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"Invalid X-Expose-Secrets header value: '{header_value}'. "
            "Valid values are: 'encrypted', 'plaintext'."
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
async def get_settings(request: Request) -> SettingsResponse:
    """Get current settings.

    Returns the persisted settings including agent configuration,
    conversation settings, and whether an LLM API key is configured.

    Use the ``X-Expose-Secrets`` header to control secret exposure:
    - ``encrypted``: Returns cipher-encrypted values (safe for frontend clients)
    - ``plaintext``: Returns raw secret values (backend clients only!)
    - (absent): Returns redacted values ("**********")

    Security:
        This endpoint requires authentication via the ``X-Session-API-Key``
        header when the server is configured with session API keys. All
        authenticated clients can access any expose mode.

        The ``plaintext`` mode should only be used by trusted backend clients
        (e.g., RemoteWorkspace) operating in the same trust domain as the
        agent-server. Frontend clients should use ``encrypted`` mode if they
        need to round-trip secret values for start conversation requests.
    """
    expose_mode = _parse_expose_secrets_header(request)
    config = _get_config(request)
    store = get_settings_store(config)
    settings = store.load() or PersistedSettings()

    # Audit log all settings access for security visibility
    client_host = request.client.host if request.client else "unknown"
    logger.info(
        "Settings accessed",
        extra={
            "client_host": client_host,
            "expose_mode": expose_mode or "redacted",
            "has_llm_api_key": settings.llm_api_key_is_set,
        },
    )

    # Build serialization context based on expose mode
    if expose_mode:
        context: dict[str, Any] = {
            "expose_secrets": expose_mode,
            "cipher": config.cipher,  # Needed for "encrypted" mode
        }
    else:
        context = {}

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
    except (ValueError, ValidationError) as e:
        # Audit log: validation failed
        # Note: PersistedSettings.update() raises ValueError (sanitized message)
        # while Pydantic validation raises ValidationError
        logger.warning(
            "Settings update validation failed",
            extra={"client_host": client_host},
        )
        # 422 Unprocessable Entity - semantic validation failure
        # Don't expose error details - could contain secrets in tracebacks
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Settings validation failed",
        )
    except RuntimeError as e:
        # Data corruption protection triggered (file exists but unreadable)
        logger.error(f"Settings update blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settings file is corrupted or encrypted with a different key",
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


@settings_router.get(SECRETS_PATH, response_model=SecretsResponse)
async def list_secrets(request: Request) -> SecretsResponse:
    """List all available secrets (names and descriptions only, no values)."""
    config = _get_config(request)
    store = get_secrets_store(config)
    secrets = store.load()

    client_host = request.client.host if request.client else "unknown"
    secret_count = len(secrets.custom_secrets) if secrets else 0
    logger.info(
        "Secrets list accessed",
        extra={"client_host": client_host, "secret_count": secret_count},
    )

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

    client_host = request.client.host if request.client else "unknown"
    if value is None:
        # Log failed access attempts to detect enumeration attacks
        logger.warning(
            "Secret access failed - not found",
            extra={"secret_name": name, "client_host": client_host},
        )
        # Use generic message to prevent secret name enumeration attacks
        raise HTTPException(status_code=404, detail="Secret not found")

    logger.info(
        "Secret accessed",
        extra={"secret_name": name, "client_host": client_host},
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

    client_host = request.client.host if request.client else "unknown"
    deleted = store.delete_secret(name)
    if not deleted:
        # Log failed deletion attempts to detect enumeration attacks
        logger.warning(
            "Secret deletion failed - not found",
            extra={"secret_name": name, "client_host": client_host},
        )
        # Use generic message to prevent secret name enumeration attacks
        raise HTTPException(status_code=404, detail="Secret not found")

    logger.info(
        "Secret deleted",
        extra={"secret_name": name, "client_host": client_host},
    )
    return {"deleted": True}
