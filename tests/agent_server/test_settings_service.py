"""Tests for the settings service."""

import tempfile
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.agent_server.settings_models import (
    NamedAgent,
    NamedLLMProfile,
    NamedSecrets,
)
from openhands.agent_server.settings_service import SettingsService
from openhands.sdk import LLM, Agent
from openhands.sdk.secret import StaticSecret
from openhands.sdk.utils.cipher import Cipher


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
    """Create an initialized settings service."""
    service = SettingsService(settings_dir=settings_dir, cipher=cipher)
    async with service:
        yield service


@pytest.fixture
def sample_llm():
    """Create a sample LLM configuration."""
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test-api-key"),
        usage_id="test-usage",
    )


@pytest.fixture
def sample_agent(sample_llm):
    """Create a sample agent configuration."""
    return Agent(llm=sample_llm, tools=[])


@pytest.mark.asyncio
async def test_llm_profile_crud(settings_service, sample_llm):
    """Test CRUD operations for LLM profiles."""
    # Create
    profile = NamedLLMProfile(name="my-claude", llm=sample_llm)
    assert settings_service.create_llm_profile(profile)

    # Read
    retrieved = settings_service.get_llm_profile("my-claude")
    assert retrieved is not None
    assert retrieved.name == "my-claude"
    assert retrieved.llm.model == "gpt-4o"

    # List
    names = settings_service.list_llm_profiles()
    assert "my-claude" in names

    # Delete
    assert settings_service.delete_llm_profile("my-claude")
    assert settings_service.get_llm_profile("my-claude") is None


@pytest.mark.asyncio
async def test_agent_crud(settings_service, sample_agent):
    """Test CRUD operations for agent configurations."""
    # Create
    agent = NamedAgent(name="my-agent", agent=sample_agent)
    assert settings_service.create_agent(agent)

    # Read
    retrieved = settings_service.get_agent("my-agent")
    assert retrieved is not None
    assert retrieved.name == "my-agent"

    # List
    names = settings_service.list_agents()
    assert "my-agent" in names

    # Delete
    assert settings_service.delete_agent("my-agent")
    assert settings_service.get_agent("my-agent") is None


@pytest.mark.asyncio
async def test_secrets_crud(settings_service):
    """Test CRUD operations for secrets bundles."""
    # Create
    secrets = NamedSecrets(
        name="my-api-keys",
        secrets={
            "GITHUB_TOKEN": StaticSecret(value=SecretStr("ghp_test123")),
            "OPENAI_KEY": StaticSecret(value=SecretStr("sk-test456")),
        },
    )
    assert settings_service.create_secrets(secrets)

    # Read
    retrieved = settings_service.get_secrets("my-api-keys")
    assert retrieved is not None
    assert retrieved.name == "my-api-keys"
    assert "GITHUB_TOKEN" in retrieved.secrets
    assert "OPENAI_KEY" in retrieved.secrets

    # List
    names = settings_service.list_secrets()
    assert "my-api-keys" in names

    # Delete
    assert settings_service.delete_secrets("my-api-keys")
    assert settings_service.get_secrets("my-api-keys") is None


@pytest.mark.asyncio
async def test_persistence(settings_dir, cipher, sample_llm):
    """Test that settings persist across service restarts."""
    # Create and save
    service1 = SettingsService(settings_dir=settings_dir, cipher=cipher)
    async with service1:
        profile = NamedLLMProfile(name="persistent-profile", llm=sample_llm)
        service1.create_llm_profile(profile)

    # Reload and verify
    service2 = SettingsService(settings_dir=settings_dir, cipher=cipher)
    async with service2:
        retrieved = service2.get_llm_profile("persistent-profile")
        assert retrieved is not None
        assert retrieved.name == "persistent-profile"


@pytest.mark.asyncio
async def test_update_existing(settings_service, sample_llm):
    """Test updating an existing profile."""
    # Create initial
    profile1 = NamedLLMProfile(name="updatable", llm=sample_llm)
    settings_service.create_llm_profile(profile1)
    initial_updated_at = profile1.updated_at

    # Update with new config
    new_llm = LLM(model="claude-sonnet-4-20250514", api_key=SecretStr("new-key"))
    profile2 = NamedLLMProfile(name="updatable", llm=new_llm)
    settings_service.create_llm_profile(profile2)

    # Verify update
    retrieved = settings_service.get_llm_profile("updatable")
    assert retrieved is not None
    assert retrieved.llm.model == "claude-sonnet-4-20250514"
    # updated_at should be different (newer)
    assert retrieved.updated_at >= initial_updated_at


@pytest.mark.asyncio
async def test_delete_nonexistent(settings_service):
    """Test deleting a nonexistent item returns False."""
    assert not settings_service.delete_llm_profile("nonexistent")
    assert not settings_service.delete_agent("nonexistent")
    assert not settings_service.delete_secrets("nonexistent")


@pytest.mark.asyncio
async def test_name_validation():
    """Test that invalid names are rejected."""
    with pytest.raises(ValueError, match="alphanumeric"):
        NamedLLMProfile(
            name="invalid name with spaces",
            llm=LLM(model="test"),
        )

    with pytest.raises(ValueError, match="alphanumeric"):
        NamedLLMProfile(
            name="invalid/name",
            llm=LLM(model="test"),
        )

    # Valid names should work
    profile = NamedLLMProfile(
        name="valid-name_123",
        llm=LLM(model="test"),
    )
    assert profile.name == "valid-name_123"
