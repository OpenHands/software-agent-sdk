"""Tests for SecretSources class."""

import json
import tempfile
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.secret import LookupSecret, StaticSecret
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def lookup_secret():
    return LookupSecret(
        url="https://my-oauth-service.com",
        headers={
            "authorization": "Bearer Token",
            "some-key": "a key",
            "not-sensitive": "hello there",
        },
    )


def test_lookup_secret_serialization_default(lookup_secret):
    """Test LookupSecret serialization"""
    dumped = lookup_secret.model_dump(mode="json")
    expected = {
        "kind": "LookupSecret",
        "description": None,
        "url": "https://my-oauth-service.com",
        "headers": {
            "authorization": "**********",
            "some-key": "**********",
            "not-sensitive": "hello there",
        },
    }
    assert dumped == expected


def test_lookup_secret_serialization_expose_secrets(lookup_secret):
    """Test LookupSecret serialization"""
    dumped = lookup_secret.model_dump(mode="json", context={"expose_secrets": True})
    expected = {
        "kind": "LookupSecret",
        "description": None,
        "url": "https://my-oauth-service.com",
        "headers": {
            "authorization": "Bearer Token",
            "some-key": "a key",
            "not-sensitive": "hello there",
        },
    }
    assert dumped == expected
    validated = LookupSecret.model_validate(dumped)
    assert validated == lookup_secret


def test_lookup_secret_serialization_encrypt(lookup_secret):
    """Test LookupSecret serialization"""
    cipher = Cipher(secret_key="some secret key")
    dumped = lookup_secret.model_dump(mode="json", context={"cipher": cipher})
    validated = LookupSecret.model_validate(dumped, context={"cipher": cipher})
    assert validated == lookup_secret


def test_static_secret_deserialization_with_missing_value():
    """Test StaticSecret can be deserialized when value field is missing.

    Regression test for issue #1505: When conversation state is serialized with
    exclude_none=True and StaticSecret.value is None (redacted), the value field
    is omitted from JSON. This caused a Pydantic validation error on resume.
    """
    # Simulate JSON stored with exclude_none=True where value was None
    json_without_value = {"kind": "StaticSecret"}

    # This should not raise a validation error
    secret = StaticSecret.model_validate(json_without_value)
    assert secret.value is None
    assert secret.get_value() is None


def test_static_secret_with_value():
    """Test StaticSecret works normally when value is provided."""
    secret = StaticSecret(value=SecretStr("my-secret"))
    assert secret.get_value() == "my-secret"

    # Round-trip with expose_secrets
    dumped = secret.model_dump(mode="json", context={"expose_secrets": True})
    assert dumped["value"] == "my-secret"

    validated = StaticSecret.model_validate(dumped)
    assert validated.get_value() == "my-secret"


def test_conversation_state_resume_with_redacted_secrets():
    """Test that conversation state can be resumed when secrets are redacted.

    Regression test for issue #1505: This reproduces the actual bug scenario where:
    1. A conversation is created with secrets in the secret_registry
    2. State is saved with exclude_none=True (which removes redacted secret values)
    3. Pod restarts and tries to resume the conversation
    4. Previously failed with: "Field required" for StaticSecret.value

    This test simulates the full serialization/deserialization cycle that happens
    in production when a conversation is saved and later resumed.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a conversation with an agent
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456780001")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        # Create initial conversation state
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )

        # Add secrets to the registry (simulating what happens in production)
        state.secret_registry.update_secrets(
            {
                "LLM_API_KEY": "secret-api-key",
                "GITHUB_TOKEN": "ghp_secret123",
                "ANOTHER_SECRET": "another-value",
            }
        )

        # Verify secrets are now in the registry as StaticSecrets
        assert "LLM_API_KEY" in state.secret_registry.secret_sources
        assert isinstance(
            state.secret_registry.secret_sources["LLM_API_KEY"], StaticSecret
        )

        # Now read the persisted base_state.json
        base_state_path = Path(persist_path) / "base_state.json"
        saved_json = json.loads(base_state_path.read_text())

        # Simulate what happens in production: secrets are saved but their values
        # get redacted (serialized as None, then excluded by exclude_none=True).
        # This creates the bug scenario where 'value' field is completely missing.
        saved_json["secret_registry"]["secret_sources"] = {
            "LLM_API_KEY": {"kind": "StaticSecret"},  # value field missing!
            "GITHUB_TOKEN": {"kind": "StaticSecret"},  # value field missing!
            "ANOTHER_SECRET": {"kind": "StaticSecret", "description": "test"},
        }

        # Write the modified state back (simulating production save)
        base_state_path.write_text(json.dumps(saved_json))

        # Now simulate pod restart - try to resume the conversation
        # This previously raised: ValidationError: Field required for StaticSecret.value
        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )

        # Verify state was resumed successfully
        assert resumed_state.id == conv_id
        assert "LLM_API_KEY" in resumed_state.secret_registry.secret_sources

        # The secret value should be None (since it was redacted/lost)
        llm_secret = resumed_state.secret_registry.secret_sources["LLM_API_KEY"]
        assert isinstance(llm_secret, StaticSecret)
        # Value is None because it was redacted during serialization
        assert llm_secret.get_value() is None
