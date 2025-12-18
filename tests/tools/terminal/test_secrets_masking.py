"""Tests for automatic secrets masking in TerminalExecutor."""

import tempfile
from unittest.mock import Mock

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM
from openhands.sdk.tool.schema import TextContent
from openhands.tools.terminal import TerminalAction, TerminalObservation
from openhands.tools.terminal.impl import TerminalExecutor


def test_terminal_executor_without_conversation():
    """Test that TerminalExecutor works normally without conversation (no masking)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create executor without conversation
        executor = TerminalExecutor(working_dir=temp_dir)

        try:
            # Execute a command that outputs a secret value
            action = TerminalAction(command="echo 'The secret is: secret-value-123'")
            result = executor(action)

            # Check that the output is not masked (no conversation provided)
            assert "secret-value-123" in result.text
            assert "<secret-hidden>" not in result.text

        finally:
            executor.close()


def test_terminal_executor_with_conversation_secrets():
    """Test TerminalExecutor uses secrets from conversation.state.secret_registry."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a conversation with secrets
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        test_secrets = {
            "SECRET_TOKEN": "secret-value-123",
            "API_KEY": "another-secret-456",
        }

        conversation = Conversation(
            agent=agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            secrets=test_secrets,
        )

        # Create executor without env_provider
        executor = TerminalExecutor(working_dir=temp_dir)

        try:
            # Mock the session to avoid subprocess issues in tests
            mock_session = Mock()
            # session.execute returns TerminalObservation
            mock_observation = TerminalObservation(
                command="echo 'Token: $SECRET_TOKEN, Key: $API_KEY'",
                exit_code=0,
                content=[
                    TextContent(text="Token: secret-value-123, Key: another-secret-456")
                ],
            )
            mock_session.execute.return_value = mock_observation
            mock_session._closed = False
            executor.session = mock_session

            # Execute command with conversation - secrets should be exported and masked
            action = TerminalAction(
                command="echo 'Token: $SECRET_TOKEN, Key: $API_KEY'"
            )
            result = executor(action, conversation=conversation)

            # Verify that session.execute was called
            assert mock_session.execute.called

            # Check that both secrets were masked in the output
            assert "secret-value-123" not in result.text
            assert "another-secret-456" not in result.text
            # SecretsManager uses <secret-hidden> as the mask
            assert "<secret-hidden>" in result.text

        finally:
            executor.close()
            conversation.close()
