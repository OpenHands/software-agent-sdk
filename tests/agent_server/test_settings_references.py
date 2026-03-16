"""Tests for settings reference resolution in conversation creation."""

import tempfile
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import (
    SettingsReferenceError,
    _resolve_agent_and_llm,
    _resolve_secrets,
)
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.settings_models import (
    NamedAgent,
    NamedLLMProfile,
    NamedSecrets,
)
from openhands.agent_server.settings_service import SettingsService
from openhands.sdk import LLM, Agent
from openhands.sdk.secret import StaticSecret
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def settings_dir():
    """Create a temporary directory for settings."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def cipher():
    """Create a cipher for encryption."""
    return Cipher("test-secret-key-12345")


@pytest.fixture
async def settings_service(settings_dir, cipher):
    """Create an initialized settings service with test data."""
    service = SettingsService(settings_dir=settings_dir, cipher=cipher)
    async with service:
        # Add some test profiles
        llm_profile = NamedLLMProfile(
            name="test-llm-profile",
            llm=LLM(model="gpt-4o", api_key=SecretStr("profile-key")),
        )
        service.create_llm_profile(llm_profile)

        agent = NamedAgent(
            name="test-agent",
            agent=Agent(
                llm=LLM(
                    model="claude-sonnet-4-20250514", api_key=SecretStr("agent-key")
                ),
                tools=[],
            ),
        )
        service.create_agent(agent)

        secrets = NamedSecrets(
            name="test-secrets",
            secrets={
                "GITHUB_TOKEN": StaticSecret(value=SecretStr("ghp_test")),
            },
        )
        service.create_secrets(secrets)

        yield service


def _create_request(
    agent: Agent | None = None,
    agent_name: str | None = None,
    llm_profile_name: str | None = None,
    secrets: dict | None = None,
    secrets_name: str | None = None,
) -> StartConversationRequest:
    """Helper to create request with minimal required fields."""
    return StartConversationRequest(
        workspace=LocalWorkspace(working_dir="workspace/project"),
        agent=agent,
        agent_name=agent_name,
        llm_profile_name=llm_profile_name,
        secrets=secrets or {},
        secrets_name=secrets_name,
    )


@pytest.mark.asyncio
async def test_resolve_inline_agent(settings_service):
    """Test that inline agent takes precedence."""
    inline_agent = Agent(llm=LLM(model="inline-model"), tools=[])
    request = _create_request(agent=inline_agent)

    resolved = _resolve_agent_and_llm(request, settings_service)
    assert resolved.llm.model == "inline-model"


@pytest.mark.asyncio
async def test_resolve_agent_by_name(settings_service):
    """Test resolving agent by reference name."""
    request = _create_request(agent_name="test-agent")

    resolved = _resolve_agent_and_llm(request, settings_service)
    assert resolved.llm.model == "claude-sonnet-4-20250514"


@pytest.mark.asyncio
async def test_resolve_agent_not_found(settings_service):
    """Test error when referenced agent doesn't exist."""
    request = _create_request(agent_name="nonexistent")

    with pytest.raises(SettingsReferenceError, match="not found"):
        _resolve_agent_and_llm(request, settings_service)


@pytest.mark.asyncio
async def test_resolve_missing_agent_and_name():
    """Test error when neither agent nor agent_name provided."""
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="workspace/project"),
        agent=None,
        agent_name=None,
    )

    with pytest.raises(SettingsReferenceError, match="must be provided"):
        _resolve_agent_and_llm(request, None)


@pytest.mark.asyncio
async def test_resolve_llm_profile(settings_service):
    """Test applying LLM profile to agent without LLM."""
    # Agent without LLM
    agent = Agent(llm=LLM(model="needs-key"), tools=[])
    request = _create_request(agent=agent, llm_profile_name="test-llm-profile")

    resolved = _resolve_agent_and_llm(request, settings_service)
    # LLM profile should be applied since agent's LLM has no api_key
    assert resolved.llm.model == "gpt-4o"


@pytest.mark.asyncio
async def test_resolve_llm_profile_not_found(settings_service):
    """Test error when LLM profile doesn't exist."""
    agent = Agent(llm=LLM(model="test"), tools=[])
    request = _create_request(agent=agent, llm_profile_name="nonexistent")

    with pytest.raises(SettingsReferenceError, match="not found"):
        _resolve_agent_and_llm(request, settings_service)


@pytest.mark.asyncio
async def test_resolve_secrets_inline_only(settings_service):
    """Test resolving only inline secrets."""
    request = _create_request(
        agent=Agent(llm=LLM(model="test"), tools=[]),
        secrets={"MY_SECRET": StaticSecret(value=SecretStr("inline"))},
    )

    resolved = _resolve_secrets(request, settings_service)
    assert "MY_SECRET" in resolved
    assert len(resolved) == 1


@pytest.mark.asyncio
async def test_resolve_secrets_by_name(settings_service):
    """Test resolving secrets by reference name."""
    request = _create_request(
        agent=Agent(llm=LLM(model="test"), tools=[]),
        secrets_name="test-secrets",
    )

    resolved = _resolve_secrets(request, settings_service)
    assert "GITHUB_TOKEN" in resolved


@pytest.mark.asyncio
async def test_resolve_secrets_merge_with_precedence(settings_service):
    """Test that inline secrets override referenced secrets."""
    request = _create_request(
        agent=Agent(llm=LLM(model="test"), tools=[]),
        secrets_name="test-secrets",
        secrets={
            "GITHUB_TOKEN": StaticSecret(value=SecretStr("inline-override")),
            "NEW_SECRET": StaticSecret(value=SecretStr("new")),
        },
    )

    resolved = _resolve_secrets(request, settings_service)
    # Check inline override took precedence
    github_secret = resolved["GITHUB_TOKEN"]
    assert isinstance(github_secret, StaticSecret)
    assert github_secret.value is not None
    assert github_secret.value.get_secret_value() == "inline-override"
    # Check new secret was added
    assert "NEW_SECRET" in resolved


@pytest.mark.asyncio
async def test_resolve_secrets_not_found(settings_service):
    """Test error when secrets bundle doesn't exist."""
    request = _create_request(
        agent=Agent(llm=LLM(model="test"), tools=[]),
        secrets_name="nonexistent",
    )

    with pytest.raises(SettingsReferenceError, match="not found"):
        _resolve_secrets(request, settings_service)


@pytest.mark.asyncio
async def test_resolve_without_settings_service():
    """Test error when settings service is None but references are used."""
    request = _create_request(agent_name="some-agent")

    with pytest.raises(SettingsReferenceError, match="not available"):
        _resolve_agent_and_llm(request, None)
