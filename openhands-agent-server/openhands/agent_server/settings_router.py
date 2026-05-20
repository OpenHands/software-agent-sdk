from collections.abc import Callable
from functools import lru_cache
from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import ValidationError

from openhands.agent_server._secrets_exposure import (
    build_expose_context,
    get_config,
    parse_expose_secrets_header,
    translate_missing_cipher,
)
from openhands.agent_server._settings_etag import (
    check_if_match,
    compute_settings_etag,
)
from openhands.agent_server.persistence import (
    SECRET_NAME_PATTERN,
    PersistedSettings,
    get_secrets_store,
    get_settings_store,
)
from openhands.agent_server.persistence.models import SettingsUpdatePayload
from openhands.sdk.logger import get_logger
from openhands.sdk.settings import (
    ConversationSettings,
    SecretCreateRequest,
    SecretItemResponse,
    SecretsListResponse,
    SettingsResponse,
    SettingsSchema,
    SettingsUpdateRequest,
    export_agent_settings_schema,
    validate_agent_settings,
)
from openhands.sdk.settings.update_models import (
    ACPAgentSettingsUpdate,
    ConversationSettingsUpdate,
    OpenHandsAgentSettingsUpdate,
    apply_agent_update,
    apply_conversation_update,
    validate_agent_settings_update,
)


logger = get_logger(__name__)

# ── Route Path Constants ─────────────────────────────────────────────────
# These are relative to the router prefix (/settings).
# When mounted on /api, full paths become /api/settings, /api/settings/secrets, etc.
# Note: RemoteWorkspace (client) uses absolute paths (e.g., "/api/settings")
# while this router uses relative paths. The paths are intentionally separate
# to match their respective contexts (router prefix vs full URL path).
SETTINGS_PATH = ""  # -> /api/settings
AGENT_SETTINGS_PATH = "/agent"  # -> /api/settings/agent  (new, typed)
CONVERSATION_SETTINGS_PATH = "/conversation"  # -> /api/settings/conversation
SECRETS_PATH = "/secrets"  # -> /api/settings/secrets
SECRET_VALUE_PATH = "/secrets/{name}"  # -> /api/settings/secrets/{name}

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


def _validate_secret_name(name: str) -> None:
    """Validate secret name format.

    Secret names must:
    - Start with a letter
    - Contain only letters, numbers, and underscores
    - Be 1-64 characters long

    Raises:
        HTTPException: 422 if name format is invalid.
    """
    if not SECRET_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Invalid secret name format. Must start with a letter, "
                "contain only letters, numbers, and underscores, "
                "and be 1-64 characters long."
            ),
        )


def _build_settings_response(
    settings: PersistedSettings, expose_mode: str | None, request: Request
) -> SettingsResponse:
    """Render :class:`PersistedSettings` into the wire response shape.

    Pulled out so that the legacy ``PATCH /api/settings`` handler, the new
    typed ``PATCH/PUT /api/settings/{agent,conversation}`` handlers, and
    ``GET /api/settings`` all serialise via the same code path.
    """
    config = get_config(request)
    context = build_expose_context(expose_mode, config.cipher)
    with translate_missing_cipher():
        return SettingsResponse(
            agent_settings=settings.agent_settings.model_dump(
                mode="json", context=context
            ),
            conversation_settings=settings.conversation_settings.model_dump(
                mode="json"
            ),
            llm_api_key_is_set=settings.llm_api_key_is_set,
        )


def _settings_response_with_etag(
    settings: PersistedSettings, expose_mode: str | None, request: Request
) -> Response:
    """Wrap a :class:`SettingsResponse` in a Response with an ``ETag`` header.

    Computed over the plaintext-canonical settings projection so two
    physical saves of identical state share the same ETag (the on-disk
    Fernet bytes change on every encryption).
    """
    payload = _build_settings_response(settings, expose_mode, request)
    etag = compute_settings_etag(settings)
    return Response(
        content=payload.model_dump_json(),
        media_type="application/json",
        headers={"ETag": etag},
    )


@settings_router.get(SETTINGS_PATH)
async def get_settings(request: Request) -> Response:
    """Get current settings.

    Returns the persisted settings including agent configuration,
    conversation settings, and whether an LLM API key is configured.

    Use the ``X-Expose-Secrets`` header to control secret exposure:
    - ``encrypted``: Returns cipher-encrypted values (safe for frontend clients)
    - ``plaintext``: Returns raw secret values (backend clients only!)
    - (absent): Returns redacted values ("**********")

    The response carries an ``ETag`` header. Clients can pass that value
    back as ``If-Match`` on subsequent ``PUT``/``PATCH`` calls (on the
    settings resource or its ``/agent`` / ``/conversation`` sub-resources)
    to detect concurrent writes and avoid silent lost updates.

    Security:
        When the server is configured with ``session_api_keys``, all endpoints
        under ``/api`` (including this one) require the ``X-Session-API-Key``
        header. When no session API keys are configured, endpoints are open.

        **Trust model:** All authenticated clients are treated as equally
        trusted. There is no role-based authorization for ``X-Expose-Secrets``
        modes—any authenticated client can request ``plaintext`` or
        ``encrypted`` exposure. This design assumes:

        - All clients sharing session API keys operate in the same trust domain
        - Network-level controls (firewalls, VPCs) restrict access to trusted
          clients only
        - Production deployments use session API keys to prevent anonymous access

        The ``plaintext`` mode exists for backend-to-backend communication
        (e.g., RemoteWorkspace). Frontend clients should prefer ``encrypted``
        mode for round-tripping secrets, or omit the header to receive redacted
        values.
    """
    expose_mode = parse_expose_secrets_header(request)
    config = get_config(request)
    store = get_settings_store(config)
    settings = store.load() or PersistedSettings()

    # Audit log all settings access for security visibility
    # Use WARNING level for plaintext mode to highlight security-sensitive operations
    client_host = request.client.host if request.client else "unknown"
    log_extra = {
        "client_host": client_host,
        "expose_mode": expose_mode or "redacted",
        "has_llm_api_key": settings.llm_api_key_is_set,
    }
    if expose_mode == "plaintext":
        logger.warning("Settings accessed with PLAINTEXT secrets", extra=log_extra)
    else:
        logger.info("Settings accessed", extra=log_extra)

    return _settings_response_with_etag(settings, expose_mode, request)


def _run_settings_update(
    request: Request,
    apply: Callable[[PersistedSettings], PersistedSettings],
    *,
    if_match: str | None,
    audit_extra: dict[str, Any],
) -> Response:
    """Shared write path for all settings mutations.

    Wraps the store's file-locked ``update`` with:

    * ``If-Match`` precondition checking — read the current state under
      the lock, hash it, compare to the caller's ``If-Match`` header. On
      mismatch return 412 with the live ETag so the client can retry
      against the new state. This is the missing optimistic-concurrency
      primitive on the legacy endpoint.
    * Uniform error translation (validation → 422, corruption → 409,
      I/O → 500), so all settings endpoints behave the same way.
    * ``ETag`` header on the response so clients always receive the new
      version stamp without a follow-up ``GET``.
    """
    config = get_config(request)
    store = get_settings_store(config)

    def _apply_with_precondition(settings: PersistedSettings) -> PersistedSettings:
        # Both the precondition and the apply run inside the store's file
        # lock, so the ETag we hash is identical to the state that the
        # write will be based on.
        check_if_match(if_match, compute_settings_etag(settings))
        return apply(settings)

    client_host = request.client.host if request.client else "unknown"
    try:
        settings = store.update(_apply_with_precondition)
    except HTTPException:
        # If-Match 412 (or any other HTTPException raised inside apply)
        raise
    except (ValueError, ValidationError):
        # PersistedSettings.update() raises ValueError (sanitized message),
        # Pydantic raises ValidationError. Both indicate a 422.
        logger.warning(
            "Settings update validation failed",
            extra={"client_host": client_host, **audit_extra},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Settings validation failed",
        )
    except RuntimeError as e:
        logger.error(f"Settings update blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settings file is corrupted or encrypted with a different key",
        )
    except (OSError, PermissionError):
        # exc_info omitted to prevent secrets in scope from leaking in tracebacks
        logger.error("Settings update failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to update settings")

    logger.info("Settings updated", extra={"client_host": client_host, **audit_extra})
    # PATCH/PUT responses redact secrets, consistent with the legacy GET default.
    return _settings_response_with_etag(settings, expose_mode=None, request=request)


@settings_router.patch(SETTINGS_PATH, response_model=SettingsResponse)
async def update_settings(
    request: Request,
    payload: SettingsUpdateRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Update settings with partial changes (legacy ``dict[str, Any]`` shape).

    .. deprecated::
        Prefer the typed endpoints below. This endpoint accepts an
        untyped ``agent_settings_diff`` / ``conversation_settings_diff``
        payload and applies them via deep merge — semantics that are hard
        for clients to reason about and impossible to discover from the
        OpenAPI schema:

        * :http:patch:`/api/settings/agent` — typed partial update for
          agent settings (validated against ``OpenHandsAgentSettingsUpdate``
          / ``ACPAgentSettingsUpdate`` with ``extra="forbid"``).
        * :http:patch:`/api/settings/conversation` — typed partial update
          for conversation settings.
        * :http:put:`/api/settings/agent` — full replace (also allows
          switching ``agent_kind``).
        * :http:put:`/api/settings/conversation` — full replace.

        New clients should not use this endpoint. It is retained for
        backwards compatibility and now also honours ``If-Match`` and
        returns an ``ETag`` header, so legacy clients can opt into
        optimistic concurrency without switching shapes.

    Accepts ``agent_settings_diff`` and/or ``conversation_settings_diff``
    for incremental updates. Values are deep-merged with existing settings.

    Uses file locking to prevent concurrent updates from overwriting each other.

    Raises:
        HTTPException: 400 if no diffs were provided, 412 on ``If-Match``
        mismatch, 422 if the update payload contains invalid values.
    """
    update_data = payload.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(
            status_code=400,
            detail=(
                "At least one of agent_settings_diff or "
                "conversation_settings_diff must be provided"
            ),
        )

    def apply_update(settings: PersistedSettings) -> PersistedSettings:
        settings.update(cast(SettingsUpdatePayload, update_data))
        return settings

    response = _run_settings_update(
        request,
        apply_update,
        if_match=if_match,
        audit_extra={
            "endpoint": "PATCH /api/settings",
            "agent_settings_modified": "agent_settings_diff" in update_data,
            "conversation_settings_modified": (
                "conversation_settings_diff" in update_data
            ),
        },
    )
    # Signal deprecation to forward-compatible clients (RFC 8594 + RFC 9745).
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        '</api/settings/agent>; rel="successor-version", '
        '</api/settings/conversation>; rel="successor-version"'
    )
    return response


# ── Typed Settings Endpoints (new) ───────────────────────────────────────
#
# These endpoints replace the untyped ``PATCH /api/settings`` for new
# clients. They split the settings resource along the same boundary as
# the persistence layer (``agent_settings`` vs ``conversation_settings``)
# and accept typed partial-update bodies validated against Pydantic
# models with ``extra="forbid"``. The ETag covers the whole settings
# resource, so cross-section concurrency is still detected.


@settings_router.put(AGENT_SETTINGS_PATH, response_model=SettingsResponse)
async def replace_agent_settings(
    request: Request,
    payload: dict,  # validated below via ``validate_agent_settings``
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Replace agent settings (full PUT).

    Accepts a complete :data:`~openhands.sdk.settings.AgentSettingsConfig`
    payload (an ``OpenHandsAgentSettings`` or ``ACPAgentSettings``
    discriminated by ``agent_kind``). The current agent settings are
    replaced wholesale. Use this when switching variants
    (``openhands`` → ``acp``); for in-variant changes, prefer
    :http:patch:`/api/settings/agent`.

    Sends ``Deprecation``-style headers? No — this is the recommended
    path. Sends an ``ETag`` reflecting the post-write state.

    Raises:
        HTTPException: 412 on ``If-Match`` mismatch, 422 if the body
            fails :data:`AgentSettingsConfig` validation.
    """
    try:
        new_agent = validate_agent_settings(payload)
    except ValidationError:
        logger.warning("PUT /api/settings/agent validation failed")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Agent settings validation failed",
        )

    def apply_replace(settings: PersistedSettings) -> PersistedSettings:
        settings.agent_settings = new_agent
        return settings

    return _run_settings_update(
        request,
        apply_replace,
        if_match=if_match,
        audit_extra={
            "endpoint": "PUT /api/settings/agent",
            "agent_kind": new_agent.agent_kind,
        },
    )


@settings_router.patch(AGENT_SETTINGS_PATH, response_model=SettingsResponse)
async def patch_agent_settings(
    request: Request,
    payload: dict,  # validated below via ``validate_agent_settings_update``
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Apply a typed partial update to agent settings.

    The request body is validated as an
    :data:`~openhands.sdk.settings.AgentSettingsUpdate` discriminated
    union (``OpenHandsAgentSettingsUpdate`` or ``ACPAgentSettingsUpdate``)
    with ``extra="forbid"``, so unknown fields fail loudly. Only fields
    the client explicitly sent are applied; the rest are preserved.

    Variant invariance
        PATCH does not switch ``agent_kind``. If the body's ``agent_kind``
        differs from the persisted variant, 422 is returned (use PUT to
        switch variants).

    Nested partials
        ``llm``, ``condenser``, ``verification`` accept their own typed
        ``*Update`` partials and are merged at the immediate top level of
        that sub-object (so e.g. ``{"llm": {"model": "..."}}`` preserves
        the existing ``api_key``).

    Raises:
        HTTPException: 412 on ``If-Match`` mismatch, 422 on validation
            or variant mismatch.
    """
    try:
        update = validate_agent_settings_update(payload)
    except ValidationError:
        logger.warning("PATCH /api/settings/agent validation failed")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Agent settings update validation failed",
        )

    def apply_partial(settings: PersistedSettings) -> PersistedSettings:
        try:
            settings.agent_settings = apply_agent_update(
                settings.agent_settings, update
            )
        except ValueError as e:
            # Variant mismatch and post-merge validation errors land here.
            # Surface as 422 with a sanitised message (no payload contents
            # — they may carry secrets through stale tracebacks).
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            )
        return settings

    return _run_settings_update(
        request,
        apply_partial,
        if_match=if_match,
        audit_extra={
            "endpoint": "PATCH /api/settings/agent",
            "fields": sorted(update.model_fields_set),
        },
    )


@settings_router.put(CONVERSATION_SETTINGS_PATH, response_model=SettingsResponse)
async def replace_conversation_settings(
    request: Request,
    payload: ConversationSettings,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Replace conversation settings (full PUT).

    Accepts a complete :class:`ConversationSettings` payload. The current
    conversation settings are replaced wholesale.

    Raises:
        HTTPException: 412 on ``If-Match`` mismatch, 422 if the body
            fails :class:`ConversationSettings` validation.
    """

    def apply_replace(settings: PersistedSettings) -> PersistedSettings:
        settings.conversation_settings = payload
        return settings

    return _run_settings_update(
        request,
        apply_replace,
        if_match=if_match,
        audit_extra={"endpoint": "PUT /api/settings/conversation"},
    )


@settings_router.patch(CONVERSATION_SETTINGS_PATH, response_model=SettingsResponse)
async def patch_conversation_settings(
    request: Request,
    payload: ConversationSettingsUpdate,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Apply a typed partial update to conversation settings.

    Body is validated as :class:`ConversationSettingsUpdate` with
    ``extra="forbid"``. Only fields the client explicitly sent are
    applied; the rest are preserved. After merge the result is
    re-validated by :class:`ConversationSettings` (so ``max_iterations >=
    1`` etc. still fire).

    Raises:
        HTTPException: 412 on ``If-Match`` mismatch, 422 on validation.
    """

    def apply_partial(settings: PersistedSettings) -> PersistedSettings:
        try:
            settings.conversation_settings = apply_conversation_update(
                settings.conversation_settings, payload
            )
        except ValidationError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Conversation settings update validation failed",
            )
        return settings

    return _run_settings_update(
        request,
        apply_partial,
        if_match=if_match,
        audit_extra={
            "endpoint": "PATCH /api/settings/conversation",
            "fields": sorted(payload.model_fields_set),
        },
    )


# Re-export the typed update model classes for OpenAPI clients that import
# from this router module directly.
_ = OpenHandsAgentSettingsUpdate, ACPAgentSettingsUpdate  # noqa: F841


# ── Secrets CRUD Endpoints ───────────────────────────────────────────────


@settings_router.get(SECRETS_PATH, response_model=SecretsListResponse)
async def list_secrets(request: Request) -> SecretsListResponse:
    """List all available secrets (names and descriptions only, no values)."""
    config = get_config(request)
    store = get_secrets_store(config)
    secrets = store.load()

    client_host = request.client.host if request.client else "unknown"
    secret_count = len(secrets.custom_secrets) if secrets else 0
    logger.info(
        "Secrets list accessed",
        extra={"client_host": client_host, "secret_count": secret_count},
    )

    if secrets is None:
        return SecretsListResponse(secrets=[])

    return SecretsListResponse(
        secrets=[
            SecretItemResponse(name=name, description=secret.description)
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

    config = get_config(request)
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


@settings_router.put(SECRETS_PATH, response_model=SecretItemResponse)
async def create_secret(
    request: Request, secret: SecretCreateRequest
) -> SecretItemResponse:
    """Create or update a custom secret (upsert).

    Raises:
        HTTPException: 400 if secret name format is invalid, 500 if file is corrupted.
    """
    _validate_secret_name(secret.name)

    config = get_config(request)
    store = get_secrets_store(config)

    try:
        store.set_secret(
            name=secret.name,
            value=secret.value.get_secret_value(),
            description=secret.description,
        )
    except RuntimeError as e:
        # Data corruption protection triggered (file exists but unreadable)
        logger.error(f"Secret create blocked: {e}")
        raise HTTPException(
            status_code=500,
            detail="Secrets file is corrupted or encrypted with a different key",
        )
    except (OSError, PermissionError):
        # Note: exc_info omitted to prevent secret values from leaking in tracebacks
        logger.error("Failed to save secret - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to save secret")

    logger.info(
        "Secret created/updated",
        extra={
            "secret_name": secret.name,
            "client_host": request.client.host if request.client else "unknown",
        },
    )
    return SecretItemResponse(name=secret.name, description=secret.description)


@settings_router.delete(SECRET_VALUE_PATH)
async def delete_secret(request: Request, name: str) -> dict[str, bool]:
    """Delete a custom secret by name.

    Raises:
        HTTPException: 400 if name format is invalid, 404 if secret not found,
        500 if file is corrupted.
    """
    _validate_secret_name(name)

    config = get_config(request)
    store = get_secrets_store(config)

    client_host = request.client.host if request.client else "unknown"
    try:
        deleted = store.delete_secret(name)
    except RuntimeError as e:
        # Data corruption protection triggered (file exists but unreadable)
        logger.error(f"Secret delete blocked: {e}")
        raise HTTPException(
            status_code=500,
            detail="Secrets file is corrupted or encrypted with a different key",
        )

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
