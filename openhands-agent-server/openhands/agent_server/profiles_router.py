"""HTTP endpoints for managing named LLM configurations (profiles)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

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
from openhands.sdk.profiles import (
    AgentProfileStore,
    ProfileReferenced,
    delete_llm_profile,
    rename_llm_profile,
)


logger = get_logger(__name__)

profiles_router = APIRouter(prefix="/profiles", tags=["Profiles"])

MAX_PROFILES = 50

ProfileName = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=PROFILE_NAME_PATTERN),
]


class ProfileInfo(BaseModel):
    name: str
    model: str | None = None
    base_url: str | None = None
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
    llm: LLM
    include_secrets: bool = Field(
        default=True,
        description="Whether to persist the API key with the profile.",
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


def _set_active_profile_if_matches(
    request: Request, old_name: str, new_name: str | None
) -> bool:
    config = get_config(request)
    settings_store = get_settings_store(config)
    settings = settings_store.load() or PersistedSettings()
    if settings.active_profile != old_name:
        return False

    def update_active(settings: PersistedSettings) -> PersistedSettings:
        settings.active_profile = new_name
        return settings

    settings_store.update(update_active)
    return True


@profiles_router.get("", response_model=ProfileListResponse)
async def list_profiles(request: Request) -> ProfileListResponse:
    """List all saved LLM profiles.

    Returns the list of profiles along with the currently active profile name,
    if one has been activated. The active_profile tracks which LLM profile
    configuration is currently in use.
    """
    config = get_config(request)
    settings_store = get_settings_store(config)
    settings = settings_store.load() or PersistedSettings()

    store = LLMProfileStore()
    with _store_errors():
        summaries = store.list_summaries()

    return ProfileListResponse(
        profiles=[ProfileInfo(**s) for s in summaries],
        active_profile=settings.active_profile,
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
            llm = store.load(name, cipher=cipher)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{name}' not found",
        )

    if expose_mode:
        context = build_expose_context(expose_mode, cipher)
        with translate_missing_cipher():
            config: dict[str, Any] = llm.model_dump(mode="json", context=context)
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
    """Save an LLM configuration as a named profile.

    Overwrites an existing profile of the same name. Returns 409 if creating
    a new profile would exceed ``MAX_PROFILES``.

    When ``OH_SECRET_KEY`` is configured, secrets are encrypted at rest.
    Clients can submit cipher-encrypted secrets which will be decrypted
    server-side before re-encrypting with the storage cipher.
    """
    cipher = get_cipher(request)
    llm = decrypt_incoming_llm_secrets(body.llm, cipher) if cipher else body.llm
    store = LLMProfileStore()
    try:
        with _store_errors():
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
async def delete_profile(
    request: Request, name: ProfileName
) -> ProfileMutationResponse:
    """Delete a saved profile (idempotent).

    Guarded by the agent-profile FK: returns 409 naming the referrers if any
    ``AgentProfile`` still cites this LLM profile via ``llm_profile_ref``.
    """
    store = LLMProfileStore()
    agent_store = AgentProfileStore()
    try:
        with _store_errors():
            delete_llm_profile(agent_store, store, name)
    except ProfileReferenced as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if _set_active_profile_if_matches(request, name, None):
        logger.info(f"Cleared active_profile for deleted profile '{name}'")
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
    setting is updated to the new name. Any ``AgentProfile.llm_profile_ref``
    citing the old name is cascaded to the new name in lock-step.
    """
    store = LLMProfileStore()
    agent_store = AgentProfileStore()
    try:
        with _store_errors():
            rename_llm_profile(agent_store, store, name, body.new_name)
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

    if name != body.new_name and _set_active_profile_if_matches(
        request, name, body.new_name
    ):
        logger.info(f"Updated active_profile from '{name}' to '{body.new_name}'")

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
    """Activate a saved LLM profile.

    This endpoint:
    1. Loads the named profile's LLM configuration
    2. Applies it to the current agent settings (updates ``agent_settings.llm``)
    3. Records the profile name as the active profile for frontend tracking

    Returns 404 if the profile does not exist.

    Use ``GET /api/profiles`` to see which profile is currently active via
    the ``active_profile`` field.
    """
    cipher = get_cipher(request)
    config = get_config(request)

    # Load the profile
    profile_store = LLMProfileStore()
    try:
        with _store_errors():
            llm = profile_store.load(name, cipher=cipher)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{name}' not found",
        )

    # Apply the LLM config to settings and record active profile
    settings_store = get_settings_store(config)

    def apply_profile(settings: PersistedSettings) -> PersistedSettings:
        # Update the LLM configuration
        llm_dict = llm.model_dump(mode="json", context={"expose_secrets": "plaintext"})
        settings.update(
            {
                "agent_settings_diff": {"llm": llm_dict},
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
