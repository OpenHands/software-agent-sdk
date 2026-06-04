"""HTTP endpoints for managing named LLM configurations (profiles)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel, Field, SecretStr

from openhands.agent_server._secrets_exposure import (
    build_expose_context,
    decrypt_incoming_llm_secrets,
    get_cipher,
    get_config,
    parse_expose_secrets_header,
    translate_missing_cipher,
)
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_settings_store,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import (
    PROFILE_NAME_PATTERN,
    LLMProfileStore,
    ProfileLimitExceeded,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.settings import ACPAgentSettings, validate_agent_settings


logger = get_logger(__name__)

profiles_router = APIRouter(prefix="/profiles", tags=["Profiles"])

MAX_PROFILES = 50

ProfileName = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=PROFILE_NAME_PATTERN),
]


class ProfileInfo(BaseModel):
    name: str
    kind: Literal["openhands", "acp"] = Field(
        default="openhands",
        description=(
            "AgentProfile kind. ``openhands`` profiles carry an LLM config; "
            "``acp`` profiles launch an ACP subprocess (see ``acp_server`` / "
            "``acp_model``). Legacy LLM profiles default to ``openhands``."
        ),
    )
    model: str | None = None
    base_url: str | None = None
    acp_server: str | None = Field(
        default=None,
        description="ACP backend key for ``acp`` profiles (else null).",
    )
    acp_model: str | None = Field(
        default=None,
        description="Configured ACP model for ``acp`` profiles (else null).",
    )
    api_key_set: bool = False


class ProfileListResponse(BaseModel):
    profiles: list[ProfileInfo]
    active_profile: str | None = None


class ProfileDetailResponse(BaseModel):
    """``config.api_key`` is always nulled; use ``api_key_set`` instead."""

    name: str
    config: dict[str, Any]
    api_key_set: bool = False


class ProfileMutationResponse(BaseModel):
    name: str
    message: str


class SaveProfileRequest(BaseModel):
    """Save an AgentProfile.

    Provide exactly one of:

    - ``llm`` — the legacy LLM-only payload, saved as an ``openhands`` profile.
      Kept for backward compatibility with existing clients.
    - ``agent_settings`` — a full AgentSettings payload (``agent_kind`` +
      fields). ``acp`` settings are saved as an ACP profile; ``openhands`` /
      ``llm`` settings persist their ``llm`` (profiles remain LLM-only for the
      OpenHands kind for now).
    """

    llm: LLM | None = None
    agent_settings: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Full AgentSettings payload (discriminated by ``agent_kind``). "
            "Use this to save ACP profiles. Mutually exclusive with ``llm``."
        ),
    )
    include_secrets: bool = Field(
        default=True,
        description="Whether to persist secrets (API key / acp_env) with the profile.",
    )


class RenameProfileRequest(BaseModel):
    new_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=PROFILE_NAME_PATTERN,
    )


@contextmanager
def _store_errors() -> Iterator[None]:
    """Map ``LLMProfileStore`` errors to HTTP responses."""
    try:
        yield
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile store is busy. Please retry.",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


def _has_api_key(llm: LLM) -> bool:
    if not isinstance(llm.api_key, SecretStr):
        return False
    return bool(llm.api_key.get_secret_value().strip())


def _acp_has_secret(acp: ACPAgentSettings) -> bool:
    """Whether an ACP profile carries credentials (acp_env or bookkeeping llm).

    ``acp_env`` is the ACP subprocess's real auth surface (e.g.
    ``ANTHROPIC_API_KEY``); a non-empty value there counts as "secret set".
    """
    if any(isinstance(v, str) and v.strip() for v in acp.acp_env.values()):
        return True
    return _has_api_key(acp.llm)


def _model_to_profile_name(model: str) -> str:
    """Convert a model name to a valid profile name.

    Transforms model names like "openai/gpt-4o" or "anthropic/claude-3-opus"
    into valid profile names by:
    - Taking just the model part after provider prefix (if present)
    - Replacing invalid characters with dashes
    - Truncating to max 64 characters
    """
    import re

    # Extract model name after provider prefix (e.g., "openai/gpt-4o" -> "gpt-4o")
    if "/" in model:
        model = model.rsplit("/", 1)[-1]

    # Replace any character that's not alphanumeric, dash, underscore, or dot
    # Profile names must match: ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "-", model)

    # Ensure it starts with alphanumeric (required by profile name pattern)
    if sanitized and not sanitized[0].isalnum():
        sanitized = "m" + sanitized

    # Truncate to max 64 characters
    sanitized = sanitized[:64]

    # Remove trailing non-alphanumeric characters
    sanitized = sanitized.rstrip("._-")

    return sanitized or "default"


@profiles_router.get("", response_model=ProfileListResponse)
async def list_profiles(request: Request) -> ProfileListResponse:
    """List all saved LLM profiles.

    Returns the list of profiles along with the currently active profile name,
    if one has been activated. The active_profile tracks which LLM profile
    configuration is currently in use.

    Auto-creates a profile named after the model if:
    - No profiles exist
    - agent_settings.llm has an API key configured

    The API key check ensures we only auto-create when the user has actually
    configured their LLM (not just relying on defaults). This allows users
    with existing LLM configurations to see their settings as a profile
    without manual creation.
    """
    cipher = get_cipher(request)
    config = get_config(request)
    settings_store = get_settings_store(config)
    settings = settings_store.load() or PersistedSettings()

    store = LLMProfileStore()
    with _store_errors():
        summaries = store.list_summaries()

    active_profile = settings.active_profile

    # Auto-create profile from existing LLM settings if no profiles exist
    # but an API key is configured. Use the model name as the profile name.
    if not summaries and settings.llm_api_key_is_set:
        llm = settings.agent_settings.llm
        profile_name = _model_to_profile_name(llm.model or "default")
        try:
            with _store_errors():
                store.save(
                    profile_name,
                    llm,
                    include_secrets=True,
                    cipher=cipher,
                )

            # Update settings to mark this as active
            def set_active(s: PersistedSettings) -> PersistedSettings:
                s.active_profile = profile_name
                return s

            settings_store.update(set_active)
            active_profile = profile_name

            # Refresh summaries to include the new profile
            summaries = store.list_summaries()
            logger.info(
                f"Auto-created '{profile_name}' profile from existing LLM settings"
            )
        except Exception as e:
            # Log but don't fail - auto-creation is a convenience feature
            logger.warning(f"Failed to auto-create profile: {e}")

    return ProfileListResponse(
        profiles=[ProfileInfo(**s) for s in summaries],
        active_profile=active_profile,
    )


@profiles_router.get("/{name}", response_model=ProfileDetailResponse)
async def get_profile(request: Request, name: ProfileName) -> ProfileDetailResponse:
    """Get a profile's configuration.

    Use the ``X-Expose-Secrets`` header to control secret exposure:
    - ``encrypted``: Returns cipher-encrypted values (safe for frontend clients)
    - ``plaintext``: Returns raw secret values (backend clients only!)
    - (absent): Returns nulled ``api_key`` with ``api_key_set`` indicator
    """
    expose_mode = parse_expose_secrets_header(request)
    cipher = get_cipher(request)

    store = LLMProfileStore()
    try:
        with _store_errors():
            kind = store.profile_kind(name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{name}' not found",
        )

    if kind == "acp":
        with _store_errors():
            acp = store.load_acp(name, cipher=cipher)
        if expose_mode:
            context = build_expose_context(expose_mode, cipher)
            with translate_missing_cipher():
                config: dict[str, Any] = acp.model_dump(mode="json", context=context)
        else:
            # No context → the model's secret serializers mask llm secrets and
            # acp_env values, so nothing sensitive leaks.
            config = acp.model_dump(mode="json")
        return ProfileDetailResponse(
            name=name, config=config, api_key_set=_acp_has_secret(acp)
        )

    with _store_errors():
        llm = store.load(name, cipher=cipher)
    if expose_mode:
        context = build_expose_context(expose_mode, cipher)
        with translate_missing_cipher():
            config = llm.model_dump(mode="json", context=context)
    else:
        config = llm.model_dump(mode="json")
        config["api_key"] = None

    return ProfileDetailResponse(
        name=name, config=config, api_key_set=_has_api_key(llm)
    )


@profiles_router.post(
    "/{name}",
    response_model=ProfileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_profile(
    request: Request,
    name: ProfileName,
    body: SaveProfileRequest,
) -> ProfileMutationResponse:
    """Save an AgentProfile (LLM or ACP) as a named profile.

    Accepts either the legacy ``llm`` payload (saved as an ``openhands``
    profile) or a full ``agent_settings`` payload (``acp`` settings are saved
    as an ACP profile). Overwrites an existing profile of the same name.
    Returns 409 if creating a new profile would exceed ``MAX_PROFILES``.

    When ``OH_SECRET_KEY`` is configured, secrets are encrypted at rest.
    Clients can submit cipher-encrypted secrets which will be decrypted
    server-side before re-encrypting with the storage cipher.
    """
    if (body.llm is None) == (body.agent_settings is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide exactly one of `llm` or `agent_settings`.",
        )

    cipher = get_cipher(request)
    store = LLMProfileStore()
    try:
        with _store_errors():
            if body.agent_settings is not None:
                # New AgentProfile path. Validate with the cipher in context so
                # client-encrypted secrets (llm.api_key, acp_env values) are
                # decrypted before re-encryption at rest.
                try:
                    settings = validate_agent_settings(
                        body.agent_settings,
                        context={"cipher": cipher} if cipher else None,
                    )
                except Exception as e:
                    # type(e).__name__ only — Pydantic errors may echo secrets.
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid agent_settings payload: {type(e).__name__}",
                    )
                if isinstance(settings, ACPAgentSettings):
                    store.save_acp(
                        name,
                        settings,
                        include_secrets=body.include_secrets,
                        cipher=cipher,
                        max_profiles=MAX_PROFILES,
                    )
                else:
                    # OpenHands AgentProfile: persist its LLM (profiles remain
                    # LLM-only for the OpenHands kind for now).
                    store.save(
                        name,
                        settings.llm,
                        include_secrets=body.include_secrets,
                        cipher=cipher,
                        max_profiles=MAX_PROFILES,
                    )
            else:
                # Legacy LLM-only payload.
                assert body.llm is not None  # guaranteed by SaveProfileRequest
                llm = (
                    decrypt_incoming_llm_secrets(body.llm, cipher)
                    if cipher
                    else body.llm
                )
                store.save(
                    name,
                    llm,
                    include_secrets=body.include_secrets,
                    cipher=cipher,
                    max_profiles=MAX_PROFILES,
                )
    except ProfileLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Profile limit reached ({MAX_PROFILES}). "
                "Delete a profile before saving a new one."
            ),
        )

    logger.info(f"Saved profile '{name}' (include_secrets={body.include_secrets})")
    return ProfileMutationResponse(name=name, message=f"Profile '{name}' saved")


@profiles_router.delete("/{name}", response_model=ProfileMutationResponse)
async def delete_profile(name: ProfileName) -> ProfileMutationResponse:
    """Delete a saved profile (idempotent)."""
    store = LLMProfileStore()
    with _store_errors():
        store.delete(name)
    logger.info(f"Deleted profile '{name}'")
    return ProfileMutationResponse(name=name, message=f"Profile '{name}' deleted")


@profiles_router.post("/{name}/rename", response_model=ProfileMutationResponse)
async def rename_profile(
    request: Request,
    name: ProfileName,
    body: RenameProfileRequest,
) -> ProfileMutationResponse:
    """Rename a saved profile atomically.

    Returns 404 if the source does not exist, or 409 if ``new_name`` already
    exists. A same-name rename is a verified no-op (still 404s if missing).

    If the renamed profile is the currently active profile, the active_profile
    setting is updated to the new name.
    """
    store = LLMProfileStore()
    try:
        with _store_errors():
            store.rename(name, body.new_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{name}' not found",
        )
    except FileExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile '{body.new_name}' already exists",
        )

    # Update active_profile if the renamed profile was the active one
    if name != body.new_name:
        config = get_config(request)
        settings_store = get_settings_store(config)
        settings = settings_store.load() or PersistedSettings()

        if settings.active_profile == name:
            new_name = body.new_name

            def update_active(s: PersistedSettings) -> PersistedSettings:
                s.active_profile = new_name
                return s

            settings_store.update(update_active)
            logger.info(f"Updated active_profile from '{name}' to '{new_name}'")

    if name == body.new_name:
        message = f"Profile '{name}' unchanged (same name)"
    else:
        message = f"Profile '{name}' renamed to '{body.new_name}'"
    logger.info(message)
    return ProfileMutationResponse(name=body.new_name, message=message)


class ActivateProfileResponse(BaseModel):
    """Response model for profile activation."""

    name: str
    message: str
    llm_applied: bool = True


@profiles_router.post("/{name}/activate", response_model=ActivateProfileResponse)
async def activate_profile(
    request: Request, name: ProfileName
) -> ActivateProfileResponse:
    """Activate a saved AgentProfile.

    This endpoint:
    1. Loads the named profile (LLM or ACP)
    2. Applies it to the current agent settings — for an ``openhands`` profile
       this updates ``agent_settings.llm``; for an ``acp`` profile it flips
       ``agent_kind`` to ``acp`` and applies the ACP launch fields
       (server/model/command/args/env)
    3. Records the profile name as the active profile for frontend tracking

    Returns 404 if the profile does not exist.

    Use ``GET /api/profiles`` to see which profile is currently active via
    the ``active_profile`` field.
    """
    cipher = get_cipher(request)
    config = get_config(request)

    # Load the profile (kind-aware)
    profile_store = LLMProfileStore()
    try:
        with _store_errors():
            kind = profile_store.profile_kind(name)
            if kind == "acp":
                acp = profile_store.load_acp(name, cipher=cipher)
                # Build the agent_settings diff from the saved ACP fields. Drop
                # ``llm`` so it doesn't deep-merge into the current bookkeeping
                # LLM, and drop ``schema_version`` / ``agent_context`` which the
                # union validator fills in. The remaining ``agent_kind: "acp"``
                # + acp_* fields flip the kind and apply the full launch config
                # (including acp_env credentials). Leftover OpenHands-only keys
                # from the current settings are ignored by ACPAgentSettings.
                acp_dump = acp.model_dump(
                    mode="json", context={"expose_secrets": "plaintext"}
                )
                agent_diff = {
                    k: v
                    for k, v in acp_dump.items()
                    if k not in ("llm", "schema_version", "agent_context")
                }
            else:
                llm = profile_store.load(name, cipher=cipher)
                # ``agent_kind: "openhands"`` flips the kind back when the
                # current settings are ACP — without it the ``{"llm": ...}``
                # diff would deep-merge into the ACP settings and stay ``acp``,
                # leaving an OpenHands profile unable to take effect. Validated
                # as OpenHandsAgentSettings, the leftover acp_* keys are ignored.
                agent_diff = {
                    "agent_kind": "openhands",
                    "llm": llm.model_dump(
                        mode="json", context={"expose_secrets": "plaintext"}
                    ),
                }
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{name}' not found",
        )

    # Apply the config to settings and record active profile
    settings_store = get_settings_store(config)

    def apply_profile(settings: PersistedSettings) -> PersistedSettings:
        settings.update(
            {
                "agent_settings_diff": agent_diff,
                "active_profile": name,
            }
        )
        return settings

    try:
        settings_store.update(apply_profile)
    except (OSError, PermissionError):
        logger.error("Failed to activate profile - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to activate profile")
    except RuntimeError as e:
        logger.error(f"Failed to activate profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settings file is corrupted or encrypted with a different key",
        )

    logger.info(f"Activated profile '{name}'")
    return ActivateProfileResponse(
        name=name,
        message=f"Profile '{name}' activated and applied to current settings",
        llm_applied=True,
    )
