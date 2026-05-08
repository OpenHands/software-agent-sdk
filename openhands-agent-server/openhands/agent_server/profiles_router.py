"""HTTP endpoints for managing named LLM configurations (profiles)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field, SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import (
    PROFILE_NAME_PATTERN,
    LLMProfileStore,
    ProfileLimitExceeded,
)
from openhands.sdk.logger import get_logger


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


@profiles_router.get("", response_model=ProfileListResponse)
async def list_profiles() -> ProfileListResponse:
    """List all saved LLM profiles."""
    store = LLMProfileStore()
    with _store_errors():
        summaries = store.list_summaries()
    return ProfileListResponse(profiles=[ProfileInfo(**s) for s in summaries])


@profiles_router.get("/{name}", response_model=ProfileDetailResponse)
async def get_profile(name: ProfileName) -> ProfileDetailResponse:
    """Get a profile's configuration with ``api_key`` nulled out."""
    store = LLMProfileStore()
    try:
        with _store_errors():
            llm = store.load(name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{name}' not found",
        )

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
    name: ProfileName,
    body: SaveProfileRequest,
) -> ProfileMutationResponse:
    """Save an LLM configuration as a named profile.

    Overwrites an existing profile of the same name. Returns 409 if creating
    a new profile would exceed ``MAX_PROFILES``.
    """
    store = LLMProfileStore()
    try:
        with _store_errors():
            store.save(
                name,
                body.llm,
                include_secrets=body.include_secrets,
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
    name: ProfileName,
    body: RenameProfileRequest,
) -> ProfileMutationResponse:
    """Rename a saved profile atomically.

    Returns 404 if the source does not exist, or 409 if ``new_name`` already
    exists. A same-name rename is a verified no-op (still 404s if missing).
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

    if name == body.new_name:
        message = f"Profile '{name}' unchanged (same name)"
    else:
        message = f"Profile '{name}' renamed to '{body.new_name}'"
    logger.info(message)
    return ProfileMutationResponse(name=body.new_name, message=message)
