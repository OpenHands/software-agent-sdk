"""HTTP CRUD + activate endpoints for meta-profiles (mirrors profiles_router).

Unlike LLM profiles, meta-profiles hold no secrets — they are plain JSON
documents persisted via :class:`MetaProfileStore`.
"""

import os
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_settings_store,
)
from openhands.sdk.llm.llm_profile_store import PROFILE_NAME_PATTERN
from openhands.sdk.llm.meta_profile_store import (
    MetaProfile,
    MetaProfileLimitExceeded,
    MetaProfileStore,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

meta_profiles_router = APIRouter(prefix="/meta-profiles", tags=["Meta-profiles"])

MAX_META_PROFILES = 50

MetaProfileName = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=PROFILE_NAME_PATTERN),
]


def _get_meta_profile_store() -> MetaProfileStore:
    """Resolve the meta-profile store under ``OH_PERSISTENCE_DIR``.

    Mirrors how the LLM/agent profile stores resolve (``OH_PERSISTENCE_DIR``
    when set, else ``~/.openhands``) so meta-profiles stay co-located with the
    LLM profiles they reference by name, and an isolated agent-server (fresh
    ``OH_PERSISTENCE_DIR``) doesn't read or write the host's
    ``~/.openhands/meta-profiles``. A bare ``MetaProfileStore()`` ignores the
    env var and always defaults to the host home dir.
    """
    env_dir = os.environ.get("OH_PERSISTENCE_DIR")
    base = pathlib.Path(env_dir) if env_dir else pathlib.Path.home() / ".openhands"
    return MetaProfileStore(base_dir=base / "meta-profiles")


class MetaProfileInfo(BaseModel):
    name: str
    classifier_model: str | None = None
    default_model: str | None = None
    num_classes: int = 0


class MetaProfileListResponse(BaseModel):
    meta_profiles: list[MetaProfileInfo]
    active_meta_profile: str | None = None


class MetaProfileDetailResponse(BaseModel):
    name: str
    config: MetaProfile


class MetaProfileMutationResponse(BaseModel):
    name: str
    message: str


class ActivateMetaProfileResponse(BaseModel):
    name: str
    message: str


@contextmanager
def _store_errors() -> Iterator[None]:
    """Map ``MetaProfileStore`` errors to HTTP responses."""
    try:
        yield
    except TimeoutError:
        # save()/delete() can raise TimeoutError from the file lock under
        # contention; surface a retryable 503 instead of a generic 500
        # (mirrors profiles_router._store_errors()).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Meta-profile store is busy. Please retry.",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


def _set_active_meta_profile_if_matches(
    request: Request, old_name: str, new_name: str | None
) -> bool:
    config = get_config(request)
    settings_store = get_settings_store(config)
    settings = settings_store.load() or PersistedSettings()
    if settings.active_meta_profile != old_name:
        return False

    def update_active(settings: PersistedSettings) -> PersistedSettings:
        # Route through PersistedSettings.update() so the change also
        # propagates into agent_settings (active_meta_profile +
        # enable_classify_and_switch_llm_tool); a direct field assignment
        # would leave that nested state stale.
        settings.update({"active_meta_profile": new_name})
        return settings

    settings_store.update(update_active)
    return True


@meta_profiles_router.get("", response_model=MetaProfileListResponse)
async def list_meta_profiles(request: Request) -> MetaProfileListResponse:
    """List all saved meta-profiles and the currently active one."""
    config = get_config(request)
    settings_store = get_settings_store(config)
    settings = settings_store.load() or PersistedSettings()

    store = _get_meta_profile_store()
    with _store_errors():
        summaries = store.list_summaries()

    return MetaProfileListResponse(
        meta_profiles=[MetaProfileInfo(**s) for s in summaries],
        active_meta_profile=settings.active_meta_profile,
    )


@meta_profiles_router.get("/{name}", response_model=MetaProfileDetailResponse)
async def get_meta_profile(name: MetaProfileName) -> MetaProfileDetailResponse:
    """Get a meta-profile's full configuration."""
    store = _get_meta_profile_store()
    try:
        with _store_errors():
            meta_profile = store.load(name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meta-profile '{name}' not found",
        )

    return MetaProfileDetailResponse(name=name, config=meta_profile)


@meta_profiles_router.post(
    "/{name}",
    response_model=MetaProfileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_meta_profile(
    name: MetaProfileName,
    body: MetaProfile,
) -> MetaProfileMutationResponse:
    """Save (create or overwrite) a meta-profile.

    Returns 409 if creating a new meta-profile would exceed
    ``MAX_META_PROFILES``.
    """
    store = _get_meta_profile_store()
    try:
        with _store_errors():
            store.save(name, body, max_profiles=MAX_META_PROFILES)
    except MetaProfileLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Meta-profile limit reached ({MAX_META_PROFILES}). "
                "Delete a meta-profile before saving a new one."
            ),
        )

    logger.info(f"Saved meta-profile '{name}'")
    return MetaProfileMutationResponse(
        name=name, message=f"Meta-profile '{name}' saved"
    )


@meta_profiles_router.delete("/{name}", response_model=MetaProfileMutationResponse)
async def delete_meta_profile(
    request: Request, name: MetaProfileName
) -> MetaProfileMutationResponse:
    """Delete a meta-profile (idempotent).

    If the deleted meta-profile is the active one, ``active_meta_profile`` is
    cleared.
    """
    store = _get_meta_profile_store()
    with _store_errors():
        store.delete(name)
    if _set_active_meta_profile_if_matches(request, name, None):
        logger.info(f"Cleared active_meta_profile for deleted meta-profile '{name}'")
    logger.info(f"Deleted meta-profile '{name}'")
    return MetaProfileMutationResponse(
        name=name, message=f"Meta-profile '{name}' deleted"
    )


@meta_profiles_router.post(
    "/{name}/activate", response_model=ActivateMetaProfileResponse
)
async def activate_meta_profile(
    request: Request, name: MetaProfileName
) -> ActivateMetaProfileResponse:
    """Activate a meta-profile by recording it as ``active_meta_profile``.

    Unlike LLM profiles, activating a meta-profile does not mutate the agent's
    LLM config — it only records which meta-profile the
    ``classify_and_switch_llm`` tool should route with. Returns 404 if the
    meta-profile does not exist.
    """
    # Verify the meta-profile exists (and is valid) before activating.
    store = _get_meta_profile_store()
    try:
        with _store_errors():
            store.load(name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meta-profile '{name}' not found",
        )

    config = get_config(request)
    settings_store = get_settings_store(config)

    def apply_active(settings: PersistedSettings) -> PersistedSettings:
        # Route through PersistedSettings.update() so activation also wires
        # agent_settings (active_meta_profile + enable_classify_and_switch_llm_tool),
        # which is what actually attaches the routing tool. A direct field
        # assignment would record the active name but never enable the tool.
        settings.update({"active_meta_profile": name})
        return settings

    try:
        settings_store.update(apply_active)
    except (OSError, PermissionError):
        logger.error("Failed to activate meta-profile - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to activate meta-profile")

    logger.info(f"Activated meta-profile '{name}'")
    return ActivateMetaProfileResponse(
        name=name, message=f"Meta-profile '{name}' activated"
    )
