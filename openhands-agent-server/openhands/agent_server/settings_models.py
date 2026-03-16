"""Models for persistent named settings in agent-server.

These models define the schema for named, reusable configurations that can be
referenced when starting conversations, rather than providing them inline each time.
"""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from openhands.agent_server.models import ACPEnabledAgent
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent
from openhands.sdk.secret import SecretSource


# Name validation pattern - alphanumeric, underscores, hyphens
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_name(name: str) -> str:
    """Validate that a name contains only allowed characters."""
    if not _NAME_PATTERN.match(name):
        raise ValueError(
            "Name must contain only alphanumeric characters, underscores, and hyphens"
        )
    return name


class NamedLLMProfile(BaseModel):
    """A named, reusable LLM configuration.

    Example:
        POST /settings/llm-profiles
        {
            "name": "my-claude",
            "llm": {
                "model": "claude-sonnet-4-20250514",
                "api_key": "sk-..."
            }
        }
    """

    name: str = Field(
        min_length=1,
        max_length=100,
        description="Unique identifier for this LLM profile",
    )
    llm: LLM = Field(description="The LLM configuration")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)


class NamedAgent(BaseModel):
    """A named, reusable agent configuration.

    Example:
        POST /settings/agents
        {
            "name": "my-agent",
            "agent": {
                "llm": {"model": "claude-sonnet-4-20250514", "api_key": "sk-..."},
                "tools": [{"name": "TerminalTool"}, {"name": "FileEditorTool"}]
            }
        }
    """

    name: str = Field(
        min_length=1,
        max_length=100,
        description="Unique identifier for this agent configuration",
    )
    agent: ACPEnabledAgent = Field(description="The agent configuration")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)


class NamedSecrets(BaseModel):
    """A named bundle of secrets that can be referenced in conversations.

    Example:
        POST /settings/secrets
        {
            "name": "my-api-keys",
            "secrets": {
                "GITHUB_TOKEN": {"kind": "StaticSecret", "value": "ghp_..."},
                "OPENAI_API_KEY": {"kind": "StaticSecret", "value": "sk-..."}
            }
        }
    """

    name: str = Field(
        min_length=1,
        max_length=100,
        description="Unique identifier for this secrets bundle",
    )
    secrets: dict[str, SecretSource] = Field(
        default_factory=dict,
        description="Dictionary of secret name to secret source",
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)


# Request/Response models for the REST API


class CreateLLMProfileRequest(BaseModel):
    """Request to create a new LLM profile."""

    name: str = Field(min_length=1, max_length=100)
    llm: LLM

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)


class CreateAgentRequest(BaseModel):
    """Request to create a new agent configuration."""

    name: str = Field(min_length=1, max_length=100)
    agent: ACPEnabledAgent

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)


class CreateSecretsRequest(BaseModel):
    """Request to create a new secrets bundle."""

    name: str = Field(min_length=1, max_length=100)
    secrets: dict[str, SecretSource] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)


class SettingsListResponse(BaseModel):
    """Response containing a list of setting names."""

    names: list[str] = Field(default_factory=list)


class LLMProfileInfo(BaseModel):
    """Public info about an LLM profile (without sensitive data)."""

    name: str
    model: str
    base_url: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_named_profile(cls, profile: NamedLLMProfile) -> "LLMProfileInfo":
        return cls(
            name=profile.name,
            model=profile.llm.model,
            base_url=profile.llm.base_url,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


class AgentInfo(BaseModel):
    """Public info about an agent configuration."""

    name: str
    llm_model: str | None = None
    tool_count: int = 0
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_named_agent(cls, agent: NamedAgent) -> "AgentInfo":
        llm_model = None
        tool_count = 0
        if isinstance(agent.agent, Agent):
            llm_model = agent.agent.llm.model if agent.agent.llm else None
            tool_count = len(agent.agent.tools) if agent.agent.tools else 0
        return cls(
            name=agent.name,
            llm_model=llm_model,
            tool_count=tool_count,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )


class SecretsInfo(BaseModel):
    """Public info about a secrets bundle (without actual secret values)."""

    name: str
    secret_names: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_named_secrets(cls, secrets: NamedSecrets) -> "SecretsInfo":
        return cls(
            name=secrets.name,
            secret_names=list(secrets.secrets.keys()),
            created_at=secrets.created_at,
            updated_at=secrets.updated_at,
        )
