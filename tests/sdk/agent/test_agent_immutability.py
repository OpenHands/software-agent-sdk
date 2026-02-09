"""Tests for Agent immutability and statelessness."""

import pytest
from pydantic import SecretStr, ValidationError

from openhands.sdk.agent.agent import Agent
from openhands.sdk.llm import LLM


class TestAgentImmutability:
    """Test Agent immutability and statelessness."""

    def setup_method(self):
        """Set up test environment."""
        self.llm: LLM = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )

    def test_agent_is_frozen(self):
        """Test that Agent instances are frozen (immutable)."""
        agent = Agent(llm=self.llm, tools=[])

        # Test that we cannot modify core fields after creation
        with pytest.raises(ValidationError, match="Instance is frozen"):
            agent.llm = "new_value"  # type: ignore[assignment]

        with pytest.raises(ValidationError, match="Instance is frozen"):
            agent.agent_context = None

        # Verify the agent remains functional after failed modification attempts
        assert agent.llm == self.llm
        assert isinstance(agent.system_message, str)
        assert len(agent.system_message) > 0

    def test_system_message_is_computed_property(self):
        """Test that system_message is computed on-demand, not stored."""
        agent = Agent(llm=self.llm, tools=[])

        # Get system message multiple times - should be consistent
        msg1 = agent.system_message
        msg2 = agent.system_message

        # Should be the same content and valid
        assert msg1 == msg2
        assert isinstance(msg1, str)
        assert len(msg1) > 0

        # Verify it's computed, not stored
        assert not hasattr(agent, "_system_message")
        assert "system_message" not in agent.__dict__

        # Basic content validation - should look like a system message
        assert any(
            keyword in msg1.lower() for keyword in ["assistant", "help", "task", "user"]
        )

    def test_condenser_property_access(self):
        """Test that condenser property works correctly."""
        # Test with None condenser
        agent1 = Agent(llm=self.llm, tools=[], condenser=None)
        assert agent1.condenser is None

        # For testing with a condenser, we'll just test that the property works
        # We don't need to test with a real condenser since that would require
        # importing and setting up the actual Condenser class

    def test_agent_properties_are_accessible(self):
        """Test that all Agent properties are accessible and return expected types."""
        agent = Agent(llm=self.llm, tools=[])

        # Test inherited properties from AgentBase
        assert agent.llm == self.llm

        assert isinstance(agent.tools, list)
        assert agent.agent_context is None
        assert agent.name == "Agent"
        assert isinstance(agent.prompt_dir, str)

        # Test Agent-specific properties
        assert isinstance(agent.system_message, str)
        assert agent.condenser is None
        assert agent.system_prompt_filename == "system_prompt.j2"

    def test_agent_is_truly_stateless(self):
        """Test that Agent doesn't store computed state."""
        agent = Agent(llm=self.llm, tools=[])

        # Access system_message multiple times
        for _ in range(3):
            msg = agent.system_message
            assert isinstance(msg, str)
            assert len(msg) > 0

        # The only fields should be the ones we explicitly defined -- i.e., those
        # in the model definition. But since some are optional (and may not be set),
        # and some are computed when models are dumped, we check that no extra
        # attributes are present beyond the defined model fields.
        expected_fields = set(Agent.model_fields.keys())
        actual_fields = set(agent.model_dump(mode="python").keys())
        computed_fields = set(Agent.model_computed_fields.keys())
        assert actual_fields - computed_fields <= expected_fields

        # Verify no additional attributes are stored
        assert not hasattr(agent, "_system_message")
        assert not hasattr(agent, "_computed_system_message")

    def test_multiple_agents_are_independent(self):
        """Test that multiple Agent instances are independent."""
        agent1 = Agent(
            llm=self.llm, tools=[], system_prompt_filename="system_prompt.j2"
        )
        agent2 = Agent(
            llm=self.llm, tools=[], system_prompt_filename="system_prompt.j2"
        )

        # They should have the same configuration
        assert agent1 == agent2
        assert agent1.system_prompt_filename == agent2.system_prompt_filename

        # But they should be different instances
        assert agent1 is not agent2

        # And their system messages should be identical (same config)
        assert agent1.system_message == agent2.system_message

    def test_agent_model_copy_creates_new_instance(self):
        """Test that model_copy creates a new Agent instance with modified fields."""
        original_agent = Agent(
            llm=self.llm,
            tools=[],
            system_prompt_kwargs={"cli_mode": True},
        )

        # Create a copy with modified fields
        modified_agent = original_agent.model_copy(
            update={"system_prompt_kwargs": {"cli_mode": False}}
        )

        # Verify that a new instance was created
        assert modified_agent is not original_agent

        # Verify that system messages are different due to different configs
        assert original_agent.system_message != modified_agent.system_message
