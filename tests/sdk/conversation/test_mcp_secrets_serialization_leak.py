"""Tests for MCP server secrets serialization security.

These tests verify that secrets expanded into mcp_servers do NOT leak through
serialization pathways (persistence, WebSocket events, API responses).

See: https://github.com/OpenHands/software-agent-sdk/pull/2873#issuecomment-4273848645
"""

import json
import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk.agent.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm import LLM
from openhands.sdk.mcp.config import coerce_mcp_servers, dump_mcp_servers
from openhands.sdk.workspace import LocalWorkspace


# A clearly identifiable secret value for testing
SECRET_VALUE = "ghp_SUPER_SECRET_TOKEN_12345_SHOULD_NOT_LEAK"


@pytest.fixture
def agent_with_secret_in_mcp_servers():
    """Create an agent with a secret value in mcp_servers.

    This simulates the state AFTER expand_mcp_variables() has resolved
    a ${GITHUB_TOKEN} placeholder to its actual secret value.
    """
    llm = LLM(model="test-model", api_key=SecretStr("test-key"))
    mcp_servers = {
        "github": {
            "command": "uvx",
            "args": ["mcp-server-github"],
            "env": {
                # This is the expanded secret - what would be in mcp_servers
                # after expand_mcp_variables() resolves ${GITHUB_TOKEN}
                "GITHUB_TOKEN": SECRET_VALUE
            },
        }
    }
    return Agent(llm=llm, mcp_servers=coerce_mcp_servers(mcp_servers))


class TestMcpSecretsDoNotLeakToPersistence:
    """Tests that mcp_servers secrets don't leak to disk persistence."""

    def test_secrets_not_in_base_state_json(
        self, agent_with_secret_in_mcp_servers, tmp_path
    ):
        """Verify that secrets in mcp_servers are NOT written to base_state.json.

        When ConversationState persists to disk, secrets that were expanded
        into mcp_servers should be excluded or redacted.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))
        persistence_dir = tmp_path / "persistence"

        # Create state (triggers persistence)
        _ = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_servers,
            workspace=workspace,
            persistence_dir=str(persistence_dir),
        )

        # Read the persisted state from disk
        base_state_path = persistence_dir / "base_state.json"
        assert base_state_path.exists(), "base_state.json should exist"

        with open(base_state_path) as f:
            persisted_data = f.read()

        # The secret value should NOT appear in the persisted file
        assert SECRET_VALUE not in persisted_data, (
            f"Secret value '{SECRET_VALUE}' was found in base_state.json! "
            "Secrets in mcp_servers should be excluded or redacted during persistence."
        )

    def test_mcp_servers_excluded_or_redacted_in_persistence(
        self, agent_with_secret_in_mcp_servers, tmp_path
    ):
        """Verify mcp_servers is handled safely in persistence.

        Either mcp_servers should be excluded entirely, or sensitive values
        within it should be redacted.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))
        persistence_dir = tmp_path / "persistence"

        # Create state (triggers persistence)
        _ = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_servers,
            workspace=workspace,
            persistence_dir=str(persistence_dir),
        )

        base_state_path = persistence_dir / "base_state.json"
        with open(base_state_path) as f:
            persisted_json = json.load(f)

        agent_data = persisted_json.get("agent", {})
        mcp_servers = agent_data.get("mcp_servers", {})

        # If mcp_servers is present, check that env values are redacted
        if mcp_servers:
            mcp_str = json.dumps(mcp_servers)
            assert SECRET_VALUE not in mcp_str, (
                "Secret value found in persisted mcp_servers! "
                "Either exclude mcp_servers or redact sensitive env values."
            )


class TestMcpSecretsDoNotLeakToWebSocket:
    """Tests that mcp_servers secrets don't leak via WebSocket events."""

    def test_secrets_not_in_state_update_event(
        self, agent_with_secret_in_mcp_servers, tmp_path
    ):
        """Verify secrets don't leak via ConversationStateUpdateEvent.

        ConversationStateUpdateEvent.from_conversation_state() serializes
        the state for WebSocket transmission. Secrets must not be included.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_servers,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
        )

        # Create the event that would be sent over WebSocket
        event = ConversationStateUpdateEvent.from_conversation_state(state)

        # Serialize the event value (this is what goes over the wire)
        event_json = json.dumps(event.value)

        assert SECRET_VALUE not in event_json, (
            f"Secret value '{SECRET_VALUE}' was found in WebSocket event! "
            "Secrets in mcp_servers should be excluded from state update events."
        )

    def test_agent_field_update_does_not_leak_secrets(
        self, agent_with_secret_in_mcp_servers, tmp_path
    ):
        """Verify secrets don't leak when agent field changes trigger callbacks.

        When state.agent is updated, the __setattr__ callback sends a
        ConversationStateUpdateEvent with the new value. This must not
        include secrets from mcp_servers.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_servers,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
        )

        # Track events sent via callback
        captured_events = []

        def capture_callback(event):
            captured_events.append(event)

        state.set_on_state_change(capture_callback)

        # Trigger an agent update (simulates what _ensure_plugins_loaded does)
        new_agent = agent_with_secret_in_mcp_servers.model_copy()
        with state:
            state.agent = new_agent

        # Check all captured events for secret leakage
        for event in captured_events:
            if hasattr(event, "value"):
                event_str = json.dumps(event.value) if event.value else ""
                assert SECRET_VALUE not in event_str, (
                    f"Secret value found in state change callback event! "
                    f"Event key: {getattr(event, 'key', 'unknown')}"
                )


class TestMcpSecretsDoNotLeakToAPI:
    """Tests that mcp_servers secrets don't leak via API responses."""

    def test_secrets_not_in_state_model_dump(
        self, agent_with_secret_in_mcp_servers, tmp_path
    ):
        """Verify secrets don't leak via state.model_dump().

        state.model_dump(mode="json") is used by API endpoints to serialize
        conversation state. Secrets in mcp_servers must be excluded.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_servers,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
        )

        # This is what API endpoints use for serialization
        state_dump = state.model_dump(mode="json")
        state_json = json.dumps(state_dump)

        assert SECRET_VALUE not in state_json, (
            f"Secret value '{SECRET_VALUE}' was found in state.model_dump()! "
            "Secrets in mcp_servers should be excluded from API responses."
        )

    def test_agent_model_dump_excludes_mcp_secrets(
        self, agent_with_secret_in_mcp_servers
    ):
        """Verify that agent.model_dump() excludes secrets from mcp_servers.

        The agent is often serialized independently. Secrets in mcp_servers
        should be excluded or redacted.
        """
        agent_dump = agent_with_secret_in_mcp_servers.model_dump(mode="json")
        agent_json = json.dumps(agent_dump)

        assert SECRET_VALUE not in agent_json, (
            f"Secret value '{SECRET_VALUE}' was found in agent.model_dump()! "
            "Secrets in mcp_servers should be excluded from serialization."
        )


class TestMcpServersPreservation:
    """Tests that verify mcp_servers functionality is preserved while secure."""

    def test_mcp_servers_still_accessible_in_memory(
        self, agent_with_secret_in_mcp_servers
    ):
        """Verify mcp_servers with secrets is still usable in memory.

        While secrets should not serialize, the in-memory mcp_servers
        should retain the secrets for actual MCP server initialization.
        """
        # The secret should be accessible in memory for actual use
        env_config = dump_mcp_servers(agent_with_secret_in_mcp_servers.mcp_servers)[
            "github"
        ]["env"]
        assert env_config["GITHUB_TOKEN"] == SECRET_VALUE, (
            "mcp_servers should retain secrets in memory for runtime use"
        )

    def test_non_secret_mcp_server_values_persist_with_cipher(self, tmp_path):
        """Verify that mcp_servers is preserved when using cipher for persistence.

        When a cipher is provided (the production flow), mcp_servers should be
        encrypted on save and decrypted on restore, preserving all values.
        """
        from openhands.sdk.utils.cipher import Cipher

        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        mcp_config = {
            "mcpServers": {
                "fetch": {
                    "command": "uvx",
                    "args": ["mcp-server-fetch"],
                    "env": {"API_KEY": "sk-mcp-secret"},
                }
            }
        }
        agent = Agent(
            llm=llm,
            mcp_servers=coerce_mcp_servers(mcp_config),
        )
        cipher = Cipher(secret_key="test-encryption-key")

        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))
        # Create state with cipher (triggers persistence with encryption)
        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
            cipher=cipher,
        )

        base_state_path = tmp_path / "persistence" / "base_state.json"
        with open(base_state_path) as f:
            persisted_json = json.load(f)

        agent_data = persisted_json.get("agent", {})

        encrypted_key = agent_data["mcp_servers"]["fetch"]["env"]["API_KEY"]
        assert encrypted_key != "sk-mcp-secret"
        decrypted_key = cipher.decrypt(encrypted_key)
        assert decrypted_key is not None
        assert decrypted_key.get_secret_value() == "sk-mcp-secret"
        assert "sk-mcp-secret" not in json.dumps(agent_data), (
            "plaintext mcp_servers secrets should not be present when encrypted"
        )

        # Verify roundtrip: restore with same cipher should get original config
        restored_state = ConversationState.create(
            id=state.id,
            agent=agent,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
            cipher=cipher,
        )
        # The runtime agent is used, but the decryption should work
        assert (
            dump_mcp_servers(restored_state.agent.mcp_servers)
            == mcp_config["mcpServers"]
        )
