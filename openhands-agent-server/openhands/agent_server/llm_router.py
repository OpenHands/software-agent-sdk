"""Router for LLM model and provider information endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from openhands.sdk.llm.utils.unverified_models import (
    _get_litellm_provider_names,
    get_supported_llm_models,
)
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS


llm_router = APIRouter(prefix="/llm", tags=["LLM"])


class ProvidersResponse(BaseModel):
    """Response containing the list of available LLM providers."""

    providers: list[str]


class ModelsResponse(BaseModel):
    """Response containing the list of available LLM models."""

    models: list[str]


class VerifiedModelsResponse(BaseModel):
    """Response containing verified models organized by provider."""

    models: dict[str, list[str]]


@llm_router.get("/providers", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """List all available LLM providers supported by LiteLLM."""
    providers = sorted(_get_litellm_provider_names())
    return ProvidersResponse(providers=providers)


@llm_router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    """List all available LLM models supported by LiteLLM.

    Note: Bedrock models are excluded unless AWS credentials are configured.
    """
    models = sorted(set(get_supported_llm_models()))
    return ModelsResponse(models=models)


@llm_router.get("/models/verified", response_model=VerifiedModelsResponse)
async def list_verified_models() -> VerifiedModelsResponse:
    """List all verified LLM models organized by provider.

    Verified models are those that have been tested and confirmed to work well
    with OpenHands.
    """
    return VerifiedModelsResponse(models=VERIFIED_MODELS)
