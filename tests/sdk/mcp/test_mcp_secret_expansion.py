"""Tests for MCP tool parameter secret/environment variable expansion.

This test file demonstrates the gap described in GitHub issue #3277:
MCP tool parameters do not expand secrets/environment variables (unlike terminal commands).

Terminal commands automatically expand $SECRET_NAME to the actual secret value,
but MCP tool parameters pass the literal string "$SECRET_NAME" to the MCP server.
"""

from typing import Any
from unittest.mock import MagicMock

import mcp.types

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.mcp.tool import MCPToolExecutor


class TestMCPToolExecutorSecretExpansion:
    """Tests for secret expansion in MCPToolExecutor.__call__.

    These tests demonstrate the current behavior gap: MCPToolExecutor does not
    expand secret references like $VAR or ${VAR} in action data, unlike
    TerminalExecutor which does expand them.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.secret_registry = SecretRegistry()
        self.secret_registry.update_secrets(
            {
                "CUSTOMER_ID": "expanded-customer",
                "API_KEY": "expanded-api-key",
            }
        )

        # Create mock conversation
        self.mock_conversation = MagicMock()
        self.mock_conversation.state.secret_registry = self.secret_registry

        # Create mock MCP client
        self.mock_client = MagicMock()
        self.mock_client.is_connected.return_value = True

        self.executor = MCPToolExecutor(
            tool_name="test_tool",
            client=self.mock_client,
        )

    def test_executor_expands_secrets_in_action_data(self):
        """Test that MCPToolExecutor expands secrets when conversation is provided.

        Currently FAILING: MCPToolExecutor passes $CUSTOMER_ID as literal string
        instead of expanding it to "expanded-customer".
        """
        # Create action with secret references
        action = MCPToolAction(
            data={
                "customer_id": "$CUSTOMER_ID",
                "api_key": "${API_KEY}",
            }
        )

        # Mock successful result
        mock_result = MagicMock(spec=mcp.types.CallToolResult)
        mock_result.content = [
            mcp.types.TextContent(type="text", text="Success")
        ]
        mock_result.isError = False

        # Track what arguments were passed to call_tool_mcp
        captured_args: dict[str, Any] = {}

        def mock_call_async_from_sync(coro_func, **kwargs):
            # Capture the action that was passed
            captured_args["action"] = kwargs.get("action")
            return MCPToolObservation.from_call_tool_result(
                tool_name="test_tool", result=mock_result
            )

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        # Execute with conversation
        self.executor(action, conversation=self.mock_conversation)

        # Verify that the action data was expanded
        expanded_action = captured_args.get("action")
        assert expanded_action is not None
        # These assertions will FAIL until the fix is implemented
        assert expanded_action.data["customer_id"] == "expanded-customer"
        assert expanded_action.data["api_key"] == "expanded-api-key"

    def test_executor_expands_braced_var_with_default(self):
        """Test that ${VAR:-default} syntax works correctly.

        Currently FAILING: MCPToolExecutor doesn't support variable expansion at all.
        """
        action = MCPToolAction(
            data={
                "existing": "${CUSTOMER_ID:-fallback}",
                "missing": "${NONEXISTENT:-default-value}",
            }
        )

        mock_result = MagicMock(spec=mcp.types.CallToolResult)
        mock_result.content = [mcp.types.TextContent(type="text", text="Success")]
        mock_result.isError = False

        captured_args: dict[str, Any] = {}

        def mock_call_async_from_sync(coro_func, **kwargs):
            captured_args["action"] = kwargs.get("action")
            return MCPToolObservation.from_call_tool_result(
                tool_name="test_tool", result=mock_result
            )

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        self.executor(action, conversation=self.mock_conversation)

        expanded_action = captured_args.get("action")
        assert expanded_action is not None
        # Existing secret should use actual value, not default
        assert expanded_action.data["existing"] == "expanded-customer"
        # Missing secret should use default value
        assert expanded_action.data["missing"] == "default-value"

    def test_executor_expands_nested_data_structures(self):
        """Test that secrets are expanded in nested dicts and lists.

        Currently FAILING: MCPToolExecutor doesn't traverse nested structures.
        """
        action = MCPToolAction(
            data={
                "auth": {
                    "customer_id": "$CUSTOMER_ID",
                    "credentials": {"token": "${API_KEY}"},
                },
                "ids": ["$CUSTOMER_ID", "static-id"],
            }
        )

        mock_result = MagicMock(spec=mcp.types.CallToolResult)
        mock_result.content = [mcp.types.TextContent(type="text", text="Success")]
        mock_result.isError = False

        captured_args: dict[str, Any] = {}

        def mock_call_async_from_sync(coro_func, **kwargs):
            captured_args["action"] = kwargs.get("action")
            return MCPToolObservation.from_call_tool_result(
                tool_name="test_tool", result=mock_result
            )

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        self.executor(action, conversation=self.mock_conversation)

        expanded_action = captured_args.get("action")
        assert expanded_action is not None
        assert expanded_action.data["auth"]["customer_id"] == "expanded-customer"
        assert expanded_action.data["auth"]["credentials"]["token"] == "expanded-api-key"
        assert expanded_action.data["ids"][0] == "expanded-customer"
        assert expanded_action.data["ids"][1] == "static-id"

    def test_executor_without_conversation_passes_literal_values(self):
        """Test that without conversation, literal values are passed through.

        This is expected behavior - no conversation means no secret registry.
        """
        action = MCPToolAction(
            data={
                "customer_id": "$CUSTOMER_ID",
                "api_key": "${API_KEY}",
            }
        )

        mock_result = MagicMock(spec=mcp.types.CallToolResult)
        mock_result.content = [
            mcp.types.TextContent(type="text", text="Success")
        ]
        mock_result.isError = False

        captured_args: dict[str, Any] = {}

        def mock_call_async_from_sync(coro_func, **kwargs):
            captured_args["action"] = kwargs.get("action")
            return MCPToolObservation.from_call_tool_result(
                tool_name="test_tool", result=mock_result
            )

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        # Execute WITHOUT conversation
        self.executor(action, conversation=None)

        # Without conversation, values should be passed as-is (no expansion)
        expanded_action = captured_args.get("action")
        assert expanded_action is not None
        # These should remain as literals since no secret registry is available
        assert expanded_action.data["customer_id"] == "$CUSTOMER_ID"
        assert expanded_action.data["api_key"] == "${API_KEY}"


class TestMCPObservationSecretMasking:
    """Tests for secret masking in MCP tool observations."""

    def setup_method(self):
        """Set up test fixtures."""
        self.secret_registry = SecretRegistry()
        self.secret_registry.update_secrets(
            {
                "API_KEY": "super-secret-key-12345",
                "PASSWORD": "my-password-abc",
            }
        )
        # Trigger export tracking by getting the values
        self.secret_registry.get_secret_value("API_KEY")
        self.secret_registry.get_secret_value("PASSWORD")

        self.mock_conversation = MagicMock()
        self.mock_conversation.state.secret_registry = self.secret_registry

    def test_observation_masks_secrets_in_output(self):
        """Test that secrets appearing in MCP observation output are masked.

        This test verifies that the SecretRegistry can mask secrets, which
        should be used by MCPToolExecutor to mask observation content.
        """
        # Create an observation that contains secret values in its output
        observation = MCPToolObservation.from_text(
            text="Response contains super-secret-key-12345 and my-password-abc",
            tool_name="test_tool",
        )

        # The secret registry should mask the secrets
        masked_text = self.secret_registry.mask_secrets_in_output(observation.text)

        assert "super-secret-key-12345" not in masked_text
        assert "my-password-abc" not in masked_text
        assert "<secret-hidden>" in masked_text
