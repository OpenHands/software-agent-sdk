"""Test that MCP tool actions don't include 'kind' field in data sent to MCP server.

This test reproduces issue #886 where the 'kind' field from DiscriminatedUnionMixin
is incorrectly included in the MCP tool arguments, causing validation errors.
"""

import pytest

from openhands.sdk.mcp import create_mcp_tools


@pytest.fixture
def fetch_tool():
    """Create a real MCP fetch tool using the mcp-server-fetch package."""
    mcp_config = {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }
    # Use longer timeout for CI environments where uvx may need to download packages
    tools = create_mcp_tools(mcp_config, timeout=120.0)
    assert len(tools) == 1
    return tools[0]


def test_real_mcp_tool_excludes_kind_field_from_action_data(fetch_tool):
    """Test that action_from_arguments doesn't include 'kind' in data field.

    This reproduces issue #886. The 'kind' field is added by DiscriminatedUnionMixin
    to dynamically created action types, but it should NOT be included in the data
    sent to the MCP server. MCP servers with additionalProperties: false will reject
    requests with unexpected 'kind' fields.
    """
    # Create action from arguments (this is what the agent does)
    args = {"url": "https://example.com"}
    action = fetch_tool.action_from_arguments(args)

    # The action.data should NOT include 'kind' field
    # because it's not part of the MCP tool schema
    assert "kind" not in action.data
    assert action.data == {"url": "https://example.com"}

    # Verify to_mcp_arguments also doesn't include 'kind'
    mcp_args = action.to_mcp_arguments()
    assert "kind" not in mcp_args
    assert mcp_args == {"url": "https://example.com"}


def test_real_mcp_tool_with_optional_field_no_kind(fetch_tool):
    """Test that optional fields work correctly without 'kind' field."""
    # Create action with both required and optional fields
    args = {"url": "https://example.com", "max_length": 5000}
    action = fetch_tool.action_from_arguments(args)

    # The action.data should NOT include 'kind' field
    assert "kind" not in action.data
    assert "url" in action.data
    assert action.data["url"] == "https://example.com"
    assert "max_length" in action.data
    assert action.data["max_length"] == 5000


def test_real_mcp_tool_drops_none_values_but_not_kind(fetch_tool):
    """Test that None values are dropped and 'kind' is not included."""
    # Create action with None value for optional field
    args = {"url": "https://example.com", "max_length": None}
    action = fetch_tool.action_from_arguments(args)

    # None should be dropped, and 'kind' should not be present
    assert "kind" not in action.data
    assert "max_length" not in action.data
    assert action.data == {"url": "https://example.com"}


def test_real_mcp_tool_execution_without_kind_field(fetch_tool):
    """Test that executing the tool works without 'kind' field in data.

    This is the ultimate test - if 'kind' was still being sent to the MCP
    server, and the server has additionalProperties: false, this would fail:
    'Input validation error: Additional properties are not allowed
    (kind was unexpected)'
    """
    # Create and execute action
    args = {"url": "https://example.com"}
    action = fetch_tool.action_from_arguments(args)

    # Execute the tool - this would fail if 'kind' was in the arguments sent to MCP
    observation = fetch_tool(action)

    # Verify we got a valid response (not an error about 'kind')
    # Check output if no error, otherwise check error message
    from openhands.sdk.llm import TextContent

    assert observation.content is not None
    # Extract text from content blocks (content is always a list now)
    text_parts = [
        block.text for block in observation.content if isinstance(block, TextContent)
    ]
    content_str = " ".join(text_parts)

    # Check that the response doesn't contain validation error about 'kind'
    if "error" in content_str.lower():
        # If there's an error, make sure it's not about 'kind' field
        assert "kind" not in content_str.lower(), (
            "MCP server rejected 'kind' field - this means the fix didn't work"
        )
