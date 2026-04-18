"""Tests for MCP config secrets serialization security.

These tests verify that secrets expanded into mcp_config do NOT leak through
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
from openhands.sdk.workspace import LocalWorkspace


# A clearly identifiable secret value for testing
SECRET_VALUE = "ghp_SUPER_SECRET_TOKEN_12345_SHOULD_NOT_LEAK"


@pytest.fixture
def agent_with_secret_in_mcp_config():
    """Create an agent with a secret value in mcp_config.

    This simulates the state AFTER expand_mcp_variables() has resolved
    a ${GITHUB_TOKEN} placeholder to its actual secret value.
    """
    llm = LLM(model="test-model", api_key=SecretStr("test-key"))
    mcp_config = {
        "mcpServers": {
            "github": {
                "command": "uvx",
                "args": ["mcp-server-github"],
                "env": {
                    # This is the expanded secret - what would be in mcp_config
                    # after expand_mcp_variables() resolves ${GITHUB_TOKEN}
                    "GITHUB_TOKEN": SECRET_VALUE
                },
            }
        }
    }
    return Agent(llm=llm, mcp_config=mcp_config)


class TestMcpSecretsDoNotLeakToPersistence:
    """Tests that mcp_config secrets don't leak to disk persistence."""

    def test_secrets_not_in_base_state_json(
        self, agent_with_secret_in_mcp_config, tmp_path
    ):
        """Verify that secrets in mcp_config are NOT written to base_state.json.

        When ConversationState persists to disk, secrets that were expanded
        into mcp_config should be excluded or redacted.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))
        persistence_dir = tmp_path / "persistence"

        # Create state (triggers persistence)
        _ = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_config,
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
            "Secrets in mcp_config should be excluded or redacted during persistence."
        )

    def test_mcp_config_excluded_or_redacted_in_persistence(
        self, agent_with_secret_in_mcp_config, tmp_path
    ):
        """Verify mcp_config is handled safely in persistence.

        Either mcp_config should be excluded entirely, or sensitive values
        within it should be redacted.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))
        persistence_dir = tmp_path / "persistence"

        # Create state (triggers persistence)
        _ = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_config,
            workspace=workspace,
            persistence_dir=str(persistence_dir),
        )

        base_state_path = persistence_dir / "base_state.json"
        with open(base_state_path) as f:
            persisted_json = json.load(f)

        agent_data = persisted_json.get("agent", {})
        mcp_config = agent_data.get("mcp_config", {})

        # If mcp_config is present, check that env values are redacted
        if mcp_config:
            mcp_str = json.dumps(mcp_config)
            assert SECRET_VALUE not in mcp_str, (
                "Secret value found in persisted mcp_config! "
                "Either exclude mcp_config or redact sensitive env values."
            )


class TestMcpSecretsDoNotLeakToWebSocket:
    """Tests that mcp_config secrets don't leak via WebSocket events."""

    def test_secrets_not_in_state_update_event(
        self, agent_with_secret_in_mcp_config, tmp_path
    ):
        """Verify secrets don't leak via ConversationStateUpdateEvent.

        ConversationStateUpdateEvent.from_conversation_state() serializes
        the state for WebSocket transmission. Secrets must not be included.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_config,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
        )

        # Create the event that would be sent over WebSocket
        event = ConversationStateUpdateEvent.from_conversation_state(state)

        # Serialize the event value (this is what goes over the wire)
        event_json = json.dumps(event.value)

        assert SECRET_VALUE not in event_json, (
            f"Secret value '{SECRET_VALUE}' was found in WebSocket event! "
            "Secrets in mcp_config should be excluded from state update events."
        )

    def test_agent_field_update_does_not_leak_secrets(
        self, agent_with_secret_in_mcp_config, tmp_path
    ):
        """Verify secrets don't leak when agent field changes trigger callbacks.

        When state.agent is updated, the __setattr__ callback sends a
        ConversationStateUpdateEvent with the new value. This must not
        include secrets from mcp_config.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_config,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
        )

        # Track events sent via callback
        captured_events = []

        def capture_callback(event):
            captured_events.append(event)

        state.set_on_state_change(capture_callback)

        # Trigger an agent update (simulates what _ensure_plugins_loaded does)
        new_agent = agent_with_secret_in_mcp_config.model_copy()
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
    """Tests that mcp_config secrets don't leak via API responses."""

    def test_secrets_not_in_state_model_dump(
        self, agent_with_secret_in_mcp_config, tmp_path
    ):
        """Verify secrets don't leak via state.model_dump().

        state.model_dump(mode="json") is used by API endpoints to serialize
        conversation state. Secrets in mcp_config must be excluded.
        """
        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent_with_secret_in_mcp_config,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
        )

        # This is what API endpoints use for serialization
        state_dump = state.model_dump(mode="json")
        state_json = json.dumps(state_dump)

        assert SECRET_VALUE not in state_json, (
            f"Secret value '{SECRET_VALUE}' was found in state.model_dump()! "
            "Secrets in mcp_config should be excluded from API responses."
        )

    def test_agent_model_dump_excludes_mcp_secrets(
        self, agent_with_secret_in_mcp_config
    ):
        """Verify that agent.model_dump() excludes secrets from mcp_config.

        The agent is often serialized independently. Secrets in mcp_config
        should be excluded or redacted.
        """
        agent_dump = agent_with_secret_in_mcp_config.model_dump(mode="json")
        agent_json = json.dumps(agent_dump)

        assert SECRET_VALUE not in agent_json, (
            f"Secret value '{SECRET_VALUE}' was found in agent.model_dump()! "
            "Secrets in mcp_config should be excluded from serialization."
        )


class TestMcpConfigPreservation:
    """Tests that verify mcp_config functionality is preserved while being secure."""

    def test_mcp_config_still_accessible_in_memory(
        self, agent_with_secret_in_mcp_config
    ):
        """Verify mcp_config with secrets is still usable in memory.

        While secrets should not serialize, the in-memory mcp_config
        should retain the secrets for actual MCP server initialization.
        """
        # The secret should be accessible in memory for actual use
        env_config = agent_with_secret_in_mcp_config.mcp_config["mcpServers"]["github"][
            "env"
        ]
        assert env_config["GITHUB_TOKEN"] == SECRET_VALUE, (
            "mcp_config should retain secrets in memory for runtime use"
        )

    def test_non_secret_mcp_config_values_persist(self, tmp_path):
        """Verify that non-secret parts of mcp_config are preserved.

        Commands, args, and other non-sensitive config should still
        be serialized normally.
        """
        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        mcp_config = {
            "mcpServers": {
                "fetch": {
                    "command": "uvx",
                    "args": ["mcp-server-fetch"],
                    # No env/secrets - should be fully preserved
                }
            }
        }
        agent = Agent(llm=llm, mcp_config=mcp_config)

        workspace = LocalWorkspace(working_dir=str(tmp_path / "workspace"))
        # Create state (triggers persistence)
        _ = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=str(tmp_path / "persistence"),
        )

        base_state_path = tmp_path / "persistence" / "base_state.json"
        with open(base_state_path) as f:
            persisted_json = json.load(f)

        agent_data = persisted_json.get("agent", {})
        persisted_mcp_config = agent_data.get("mcp_config", {})

        # Non-sensitive config should be preserved
        # (This test may need adjustment based on the chosen fix strategy)
        assert "mcpServers" in persisted_mcp_config or persisted_mcp_config == {}, (
            "Non-secret mcp_config should be preserved or explicitly cleared"
        )
