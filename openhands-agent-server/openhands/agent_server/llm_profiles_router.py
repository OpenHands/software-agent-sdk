"""Router for persisted LLM profile CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from openhands.agent_server.config import Config
from openhands.agent_server.dependencies import get_config
from openhands.agent_server.models import Success
from openhands.sdk import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


llm_profiles_router = APIRouter(prefix="/llm-profiles", tags=["LLM Profiles"])


class UpsertLLMProfileRequest(BaseModel):
    """Request payload for creating or replacing an LLM profile."""

    llm: LLM


class LLMProfileResponse(BaseModel):
    """Response containing a single named LLM profile."""

    id: str = Field(description="Profile identifier")
    llm: LLM


class LLMProfileListResponse(BaseModel):
    """Response containing all named LLM profiles."""

    profiles: list[LLMProfileResponse]


def _get_profile_store() -> LLMProfileStore:
    return LLMProfileStore()


def _has_persisted_secret(llm: LLM) -> bool:
    return any(
        secret is not None
        for secret in (
            llm.api_key,
            llm.aws_access_key_id,
            llm.aws_secret_access_key,
            llm.aws_session_token,
        )
    )


def _load_profile_response(profile_id: str, config: Config) -> LLMProfileResponse:
    try:
        llm = _get_profile_store().load(profile_id, cipher=config.cipher)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return LLMProfileResponse(id=profile_id.removesuffix(".json"), llm=llm)


@llm_profiles_router.get("", response_model=LLMProfileListResponse)
async def list_llm_profiles(
    config: Config = Depends(get_config),
) -> LLMProfileListResponse:
    """List all persisted LLM profiles."""
    profiles = [
        _load_profile_response(profile.removesuffix(".json"), config)
        for profile in _get_profile_store().list()
    ]
    return LLMProfileListResponse(profiles=profiles)


@llm_profiles_router.get("/{profile_id}", response_model=LLMProfileResponse)
async def get_llm_profile(
    profile_id: str,
    config: Config = Depends(get_config),
) -> LLMProfileResponse:
    """Get a persisted LLM profile by name."""
    return _load_profile_response(profile_id, config)


@llm_profiles_router.put("/{profile_id}", response_model=LLMProfileResponse)
async def put_llm_profile(
    profile_id: str,
    request: UpsertLLMProfileRequest,
    config: Config = Depends(get_config),
) -> LLMProfileResponse:
    """Create or replace a persisted LLM profile."""
    if _has_persisted_secret(request.llm) and config.cipher is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "OH_SECRET_KEY must be configured to store LLM profiles with secrets."
            ),
        )

    try:
        _get_profile_store().save(
            profile_id,
            request.llm,
            include_secrets=True,
            cipher=config.cipher,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _load_profile_response(profile_id, config)


@llm_profiles_router.delete("/{profile_id}")
async def delete_llm_profile(profile_id: str) -> Success:
    """Delete a persisted LLM profile."""
    try:
        _get_profile_store().delete(profile_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Success()
