"""Tests for agent integration with secrets manager."""

from typing import cast
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM
from openhands.sdk.secret import LookupSecret, SecretSource, StaticSecret
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.impl import TerminalExecutor


# -----------------------
# Fixtures
# -----------------------


@pytest.fixture
def llm() -> LLM:
    return LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")


@pytest.fixture
def tools() -> list[Tool]:
    register_tool("TerminalTool", TerminalTool)
    return [Tool(name="TerminalTool")]


@pytest.fixture
def agent(llm: LLM, tools: list[Tool]) -> Agent:
    return Agent(llm=llm, tools=tools)


@pytest.fixture
def conversation(agent: Agent, tmp_path) -> LocalConversation:
    return LocalConversation(agent, workspace=str(tmp_path))


@pytest.fixture
def terminal_executor(conversation: LocalConversation) -> TerminalExecutor:
    # Trigger lazy initialization before accessing tools_map
    conversation._ensure_agent_ready()
    tools_map = conversation.agent.tools_map
    terminal_tool = tools_map["terminal"]
    return cast(TerminalExecutor, terminal_tool.executor)


@pytest.fixture
def agent_no_bash(llm: LLM) -> Agent:
    return Agent(llm=llm, tools=[])


@pytest.fixture
def conversation_no_bash(agent_no_bash: Agent, tmp_path) -> LocalConversation:
    return LocalConversation(agent_no_bash, workspace=str(tmp_path))


def test_agent_configures_bash_tools_env_provider(
    conversation: LocalConversation, terminal_executor: TerminalExecutor, agent: Agent
):
    """Test that bash executor works with conversation secrets."""
    # Add secrets to conversation
    conversation.update_secrets(
        {
            "API_KEY": "test-api-key",
            "DB_PASSWORD": "test-password",
        }
    )

    # Get the bash tool from agent
    bash_tool = agent.tools_map["terminal"]

    assert bash_tool is not None
    assert bash_tool.executor is not None

    # Test that secrets are accessible via conversation
    secret_registry = conversation.state.secret_registry
    env_vars = secret_registry.get_secrets_as_env_vars("echo $API_KEY")
    assert env_vars == {"API_KEY": "test-api-key"}

    env_vars = secret_registry.get_secrets_as_env_vars("echo $NOT_A_KEY")
    assert env_vars == {}


def test_agent_env_provider_with_callable_secrets(
    conversation: LocalConversation, terminal_executor: TerminalExecutor
):
    """Test that conversation secrets work with callable secrets."""

    # Add callable secrets
    class MySecretSource(SecretSource):
        def get_value(self):
            return "dynamic-token-123"

    conversation.update_secrets(
        {
            "STATIC_KEY": "static-value",
            "DYNAMIC_TOKEN": MySecretSource(),
        }
    )

    secret_registry = conversation.state.secret_registry
    env_vars = secret_registry.get_secrets_as_env_vars(
        "export DYNAMIC_TOKEN=$DYNAMIC_TOKEN"
    )
    assert env_vars == {"DYNAMIC_TOKEN": "dynamic-token-123"}


def test_agent_env_provider_handles_exceptions(
    conversation: LocalConversation, terminal_executor: TerminalExecutor
):
    """Test that conversation secrets handle exceptions gracefully."""

    # Add a failing callable secret
    class MyFailingSecretSource(SecretSource):
        def get_value(self):
            raise ValueError("Secret retrieval failed")

    conversation.update_secrets(
        {
            "WORKING_KEY": "working-value",
            "FAILING_KEY": MyFailingSecretSource(),
        }
    )

    secret_registry = conversation.state.secret_registry

    # Should not raise exception, should return empty dict
    env_vars = secret_registry.get_secrets_as_env_vars(
        "export FAILING_KEY=$FAILING_KEY"
    )
    assert env_vars == {}

    # Working key should still work
    env_vars = secret_registry.get_secrets_as_env_vars(
        "export WORKING_KEY=$WORKING_KEY"
    )
    assert env_vars == {"WORKING_KEY": "working-value"}


def test_agent_env_provider_no_matches(
    conversation: LocalConversation, terminal_executor: TerminalExecutor
):
    """Test conversation secrets when command has no secret matches."""

    conversation.update_secrets({"API_KEY": "test-value"})

    # Test secrets manager with command that doesn't reference secrets
    secret_registry = conversation.state.secret_registry
    env_vars = secret_registry.get_secrets_as_env_vars("echo hello world")

    assert env_vars == {}


def test_agent_without_bash_throws_warning(llm):
    """Test that agent works correctly when no bash tools are present."""
    # This test is no longer relevant since we removed
    # _configure_bash_tools_env_provider
    # Agent no longer logs warnings about missing bash tools
    # Creating conversation without bash tools should work fine
    conversation = Conversation(agent=Agent(llm=llm, tools=[]))
    assert conversation is not None
    conversation.close()


def test_agent_secrets_integration_workflow(
    conversation: LocalConversation, terminal_executor: TerminalExecutor, agent: Agent
):
    """Test complete workflow of conversation secrets integration."""

    # Add secrets with mixed types

    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = "bearer-token-456"

        conversation.update_secrets(
            {
                "API_KEY": "static-api-key-123",
                "AUTH_TOKEN": LookupSecret(url="https://my-idp.com/"),
                "DATABASE_URL": "postgresql://localhost/test",
            }
        )

        secret_registry = conversation.state.secret_registry

        # Single secret
        env_vars = secret_registry.get_secrets_as_env_vars(
            "curl -H 'X-API-Key: $API_KEY'"
        )
        assert env_vars == {"API_KEY": "static-api-key-123"}

        # Multiple secrets
        command = "export API_KEY=$API_KEY && export AUTH_TOKEN=$AUTH_TOKEN"
        env_vars = secret_registry.get_secrets_as_env_vars(command)
        assert env_vars == {
            "API_KEY": "static-api-key-123",
            "AUTH_TOKEN": "bearer-token-456",
        }

        # No secrets referenced
        env_vars = secret_registry.get_secrets_as_env_vars("echo hello world")
        assert env_vars == {}

    # Step 5: Update secrets and verify changes propagate
    conversation.update_secrets({"API_KEY": "updated-api-key-789"})

    secret_registry = conversation.state.secret_registry
    env_vars = secret_registry.get_secrets_as_env_vars("curl -H 'X-API-Key: $API_KEY'")
    assert env_vars == {"API_KEY": "updated-api-key-789"}


def test_mask_secrets(
    conversation: LocalConversation, terminal_executor: TerminalExecutor, agent: Agent
):
    """Test that bash executor masks secrets when conversation is passed."""

    class MyDynamicSecretSource(SecretSource):
        def get_value(self):
            return "dynamic-secret"

    # Add secrets to conversation
    conversation.update_secrets(
        {
            "API_KEY": "test-api-key",
            "DB_PASSWORD": MyDynamicSecretSource(),
        }
    )

    try:
        action = TerminalAction(command="echo $API_KEY")
        result = terminal_executor(action, conversation=conversation)
        assert "test-api-key" not in result.text
        assert "<secret-hidden>" in result.text

        action = TerminalAction(command="echo $DB_PASSWORD")
        result = terminal_executor(action, conversation=conversation)
        assert "dynamic-secret" not in result.text
        assert "<secret-hidden>" in result.text

    finally:
        terminal_executor.close()


def test_mask_changing_secrets(
    conversation: LocalConversation, terminal_executor: TerminalExecutor, agent: Agent
):
    class MyChangingDynamicSecretSource(SecretSource):
        counter: int = 0

        def get_value(self):
            self.counter += 1
            return f"changing-secret-{self.counter}"

    conversation.update_secrets(
        {
            "DB_PASSWORD": MyChangingDynamicSecretSource(),
        }
    )

    try:
        action = TerminalAction(command="echo $DB_PASSWORD")
        result = terminal_executor(action, conversation=conversation)
        assert "changing-secret" not in result.text
        assert "<secret-hidden>" in result.text

        action = TerminalAction(command="echo $DB_PASSWORD")
        result = terminal_executor(action, conversation=conversation)
        assert "changing-secret" not in result.text
        assert "<secret-hidden>" in result.text

    finally:
        terminal_executor.close()


def test_masking_persists(
    conversation: LocalConversation, terminal_executor: TerminalExecutor, agent: Agent
):
    class MyChangingFailingDynamicSecretSource(SecretSource):
        counter: int = 0
        raised_on_second: bool = False

        def get_value(self):
            self.counter += 1
            if self.counter == 1:
                return f"changing-secret-{self.counter}"
            else:
                self.raised_on_second = True
                raise Exception("Blip occured, failed to refresh token")

    dynamic_secret = MyChangingFailingDynamicSecretSource()
    conversation.update_secrets(
        {
            "DB_PASSWORD": dynamic_secret,
        }
    )

    try:
        action = TerminalAction(command="echo $DB_PASSWORD")
        result = terminal_executor(action, conversation=conversation)
        print(result)
        assert "changing-secret" not in result.text
        assert "<secret-hidden>" in result.text

        action = TerminalAction(command="echo $DB_PASSWORD")
        result = terminal_executor(action, conversation=conversation)
        assert "changing-secret" not in result.text
        assert "<secret-hidden>" in result.text
        assert dynamic_secret.raised_on_second

    finally:
        terminal_executor.close()


# -----------------------
# Tests for secrets in system prompt
# -----------------------


def test_update_secrets_adds_to_registry(conversation: LocalConversation):
    """Test that update_secrets adds secrets to the secret_registry."""
    # Add secrets
    conversation.update_secrets(
        {
            "API_KEY": StaticSecret(
                value=SecretStr("test-key"), description="API authentication key"
            ),
            "DB_PASSWORD": "plain-secret-value",
        }
    )

    # Verify secrets are in secret_registry
    secret_infos = conversation.state.secret_registry.get_secret_infos()
    secret_names = [s["name"] for s in secret_infos]
    assert "API_KEY" in secret_names
    assert "DB_PASSWORD" in secret_names


def test_update_secrets_appears_in_dynamic_context(conversation: LocalConversation):
    """Test that secrets added via update_secrets appear in agent's dynamic context."""
    # Add secrets with descriptions
    conversation.update_secrets(
        {
            "GITHUB_TOKEN": StaticSecret(
                value=SecretStr("ghp_xxx"), description="GitHub authentication token"
            ),
            "OPENAI_API_KEY": StaticSecret(
                value=SecretStr("sk-xxx"), description="OpenAI API key for LLM calls"
            ),
        }
    )

    # Agent pulls secrets from state when building dynamic context
    agent = cast(Agent, conversation.agent)
    dynamic_context = agent.get_dynamic_context(conversation.state)

    # Verify secrets appear in the dynamic context
    assert dynamic_context is not None
    assert "<CUSTOM_SECRETS>" in dynamic_context
    assert "GITHUB_TOKEN" in dynamic_context
    assert "GitHub authentication token" in dynamic_context
    assert "OPENAI_API_KEY" in dynamic_context
    assert "OpenAI API key for LLM calls" in dynamic_context
    assert "</CUSTOM_SECRETS>" in dynamic_context


def test_secrets_merges_with_existing_context(llm: LLM, tmp_path):
    """Test that registry secrets merge with existing agent_context secrets."""
    # Create agent with existing context and secrets
    existing_secrets = {
        "EXISTING_SECRET": StaticSecret(
            value=SecretStr("existing-value"), description="Pre-existing secret"
        ),
    }
    agent = Agent(
        llm=llm,
        tools=[],
        agent_context=AgentContext(
            secrets=existing_secrets,
            system_message_suffix="Custom instructions here",
        ),
    )
    conversation = LocalConversation(agent, workspace=str(tmp_path))

    # Add new secrets via update_secrets (goes to registry)
    conversation.update_secrets(
        {
            "NEW_SECRET": StaticSecret(
                value=SecretStr("new-value"), description="Newly added secret"
            ),
        }
    )

    # Agent should merge secrets from agent_context and registry
    dynamic_context = agent.get_dynamic_context(conversation.state)

    # Both secrets should appear in dynamic context
    assert dynamic_context is not None
    assert "EXISTING_SECRET" in dynamic_context
    assert "Pre-existing secret" in dynamic_context
    assert "NEW_SECRET" in dynamic_context
    assert "Newly added secret" in dynamic_context

    # Verify existing context properties are preserved
    assert "Custom instructions here" in dynamic_context

    conversation.close()


def test_update_secrets_overrides_existing_secret(conversation: LocalConversation):
    """Test that update_secrets overrides existing secrets with the same key."""
    # Add initial secret
    conversation.update_secrets(
        {
            "API_KEY": StaticSecret(
                value=SecretStr("old-key"), description="Old description"
            ),
        }
    )

    # Update with new value
    conversation.update_secrets(
        {
            "API_KEY": StaticSecret(
                value=SecretStr("new-key"), description="New description"
            ),
        }
    )

    # Verify the secret was updated in dynamic context
    agent = cast(Agent, conversation.agent)
    dynamic_context = agent.get_dynamic_context(conversation.state)
    assert dynamic_context is not None
    assert "New description" in dynamic_context


def test_secrets_via_constructor_appear_in_prompt(llm: LLM, tmp_path):
    """Test that secrets passed via constructor appear in the prompt."""
    agent = Agent(llm=llm, tools=[])
    secrets = {
        "CONSTRUCTOR_SECRET": StaticSecret(
            value=SecretStr("constructor-value"),
            description="Secret passed via constructor",
        ),
    }
    conversation = LocalConversation(agent, workspace=str(tmp_path), secrets=secrets)

    # Verify secrets are in registry
    secret_infos = conversation.state.secret_registry.get_secret_infos()
    secret_names = [s["name"] for s in secret_infos]
    assert "CONSTRUCTOR_SECRET" in secret_names

    # Verify secrets appear in dynamic context
    dynamic_context = agent.get_dynamic_context(conversation.state)
    assert dynamic_context is not None
    assert "CONSTRUCTOR_SECRET" in dynamic_context
    assert "Secret passed via constructor" in dynamic_context

    conversation.close()
