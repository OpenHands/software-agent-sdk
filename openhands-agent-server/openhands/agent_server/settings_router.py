"""REST API router for managing persistent settings.

Provides endpoints for CRUD operations on:
- LLM profiles: Named, reusable LLM configurations
- Agents: Named, reusable agent configurations
- Secrets: Named bundles of secrets
"""

from fastapi import APIRouter, Depends, HTTPException, status

from openhands.agent_server.models import Success
from openhands.agent_server.settings_models import (
    AgentInfo,
    CreateAgentRequest,
    CreateLLMProfileRequest,
    CreateSecretsRequest,
    LLMProfileInfo,
    NamedAgent,
    NamedLLMProfile,
    NamedSecrets,
    SecretsInfo,
    SettingsListResponse,
)
from openhands.agent_server.settings_service import SettingsService


settings_router = APIRouter(prefix="/settings", tags=["Settings"])


# Dependency to get the settings service
def get_settings_service() -> SettingsService:
    from openhands.agent_server.settings_service import get_default_settings_service

    return get_default_settings_service()


# === LLM Profiles ===


@settings_router.get("/llm-profiles")
async def list_llm_profiles(
    service: SettingsService = Depends(get_settings_service),
) -> SettingsListResponse:
    """List all stored LLM profile names."""
    names = service.list_llm_profiles()
    return SettingsListResponse(names=names)


@settings_router.get(
    "/llm-profiles/{name}",
    responses={404: {"description": "LLM profile not found"}},
)
async def get_llm_profile(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> NamedLLMProfile:
    """Get an LLM profile by name.

    Returns the full profile configuration including the LLM settings.
    Note: API keys and other secrets are encrypted in storage but returned
    in their encrypted form for security.
    """
    profile = service.get_llm_profile(name)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM profile '{name}' not found",
        )
    return profile


@settings_router.get("/llm-profiles/{name}/info")
async def get_llm_profile_info(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> LLMProfileInfo:
    """Get public info about an LLM profile (without sensitive data)."""
    profile = service.get_llm_profile(name)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM profile '{name}' not found",
        )
    return LLMProfileInfo.from_named_profile(profile)


@settings_router.post("/llm-profiles", status_code=status.HTTP_201_CREATED)
async def create_llm_profile(
    request: CreateLLMProfileRequest,
    service: SettingsService = Depends(get_settings_service),
) -> NamedLLMProfile:
    """Create or update an LLM profile.

    If a profile with the given name already exists, it will be updated.
    """
    profile = NamedLLMProfile(name=request.name, llm=request.llm)
    if not service.create_llm_profile(profile):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create LLM profile",
        )
    return profile


@settings_router.delete(
    "/llm-profiles/{name}",
    responses={404: {"description": "LLM profile not found"}},
)
async def delete_llm_profile(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> Success:
    """Delete an LLM profile by name."""
    if not service.delete_llm_profile(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM profile '{name}' not found",
        )
    return Success()


# === Agents ===


@settings_router.get("/agents")
async def list_agents(
    service: SettingsService = Depends(get_settings_service),
) -> SettingsListResponse:
    """List all stored agent names."""
    names = service.list_agents()
    return SettingsListResponse(names=names)


@settings_router.get(
    "/agents/{name}",
    responses={404: {"description": "Agent not found"}},
)
async def get_agent(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> NamedAgent:
    """Get an agent configuration by name."""
    agent = service.get_agent(name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )
    return agent


@settings_router.get("/agents/{name}/info")
async def get_agent_info(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> AgentInfo:
    """Get public info about an agent configuration."""
    agent = service.get_agent(name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )
    return AgentInfo.from_named_agent(agent)


@settings_router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: CreateAgentRequest,
    service: SettingsService = Depends(get_settings_service),
) -> NamedAgent:
    """Create or update an agent configuration.

    If an agent with the given name already exists, it will be updated.
    """
    agent = NamedAgent(name=request.name, agent=request.agent)
    if not service.create_agent(agent):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create agent",
        )
    return agent


@settings_router.delete(
    "/agents/{name}",
    responses={404: {"description": "Agent not found"}},
)
async def delete_agent(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> Success:
    """Delete an agent configuration by name."""
    if not service.delete_agent(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )
    return Success()


# === Secrets ===


@settings_router.get("/secrets")
async def list_secrets(
    service: SettingsService = Depends(get_settings_service),
) -> SettingsListResponse:
    """List all stored secrets bundle names."""
    names = service.list_secrets()
    return SettingsListResponse(names=names)


@settings_router.get(
    "/secrets/{name}",
    responses={404: {"description": "Secrets bundle not found"}},
)
async def get_secrets(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> NamedSecrets:
    """Get a secrets bundle by name.

    Note: Secret values are encrypted in storage. The returned structure
    includes the encrypted values.
    """
    secrets = service.get_secrets(name)
    if secrets is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secrets bundle '{name}' not found",
        )
    return secrets


@settings_router.get("/secrets/{name}/info")
async def get_secrets_info(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> SecretsInfo:
    """Get public info about a secrets bundle (keys only, no values)."""
    secrets = service.get_secrets(name)
    if secrets is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secrets bundle '{name}' not found",
        )
    return SecretsInfo.from_named_secrets(secrets)


@settings_router.post("/secrets", status_code=status.HTTP_201_CREATED)
async def create_secrets(
    request: CreateSecretsRequest,
    service: SettingsService = Depends(get_settings_service),
) -> NamedSecrets:
    """Create or update a secrets bundle.

    If a bundle with the given name already exists, it will be updated.
    """
    secrets = NamedSecrets(name=request.name, secrets=request.secrets)
    if not service.create_secrets(secrets):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create secrets bundle",
        )
    return secrets


@settings_router.delete(
    "/secrets/{name}",
    responses={404: {"description": "Secrets bundle not found"}},
)
async def delete_secrets(
    name: str,
    service: SettingsService = Depends(get_settings_service),
) -> Success:
    """Delete a secrets bundle by name."""
    if not service.delete_secrets(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secrets bundle '{name}' not found",
        )
    return Success()
