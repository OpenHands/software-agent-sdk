"""Tests for MCP tool functionality with new simplified implementation."""

from typing import Any
from unittest.mock import MagicMock, Mock

import mcp.types

from openhands.sdk.event import Event, ObservationEvent
from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.definition import MCPToolObservation
from openhands.sdk.mcp.tool import MCPToolDefinition, MCPToolExecutor
from openhands.sdk.tool import ToolAnnotations
from openhands.sdk.utils.async_executor import AsyncExecutor


# Serialized by SDK 1.44.1, the last release without the structured result
# fields. Hardcoded so future model changes cannot silently rewrite the
# legacy shape the loader must keep accepting.
V1_44_1_EVENT_JSON = (
    '{"id":"f33b8b11-aa29-47e5-b658-bcab8ab9dd21",'
    '"timestamp":"2026-08-30T16:04:53.196016","source":"environment",'
    '"tool_name":"legacy_tool","tool_call_id":"call-1",'
    '"observation":{"content":[],"is_error":false,"tool_name":"legacy_tool",'
    '"kind":"MCPToolObservation"},"action_id":"action-1","extended_content":[],'
    '"kind":"ObservationEvent"}'
)


class MockMCPClient(MCPClient):
    """Mock MCPClient for testing that bypasses the complex constructor."""

    def __init__(self):
        # Initialize only the fields needed for sync execution (no transport)
        self._executor = AsyncExecutor()
        self._closed = False
        self._tools = []


class TestMCPToolObservation:
    """Test MCPToolObservation functionality."""

    def test_from_call_tool_result_success(self):
        """Test creating observation from successful MCP result."""
        # Create mock MCP result
        result = MagicMock(spec=mcp.types.CallToolResult)
        result.content = [
            mcp.types.TextContent(type="text", text="Operation completed successfully")
        ]
        result.isError = False
        result.structuredContent = None
        result.meta = None

        observation = MCPToolObservation.from_call_tool_result(
            tool_name="test_tool", result=result
        )

        assert observation.tool_name == "test_tool"
        assert observation.content is not None
        assert len(observation.content) == 2
        assert isinstance(observation.content[0], TextContent)
        assert observation.content[0].text == "[Tool 'test_tool' executed.]"
        assert isinstance(observation.content[1], TextContent)
        assert observation.content[1].text == "Operation completed successfully"
        assert observation.is_error is False

    def test_from_call_tool_result_error(self):
        """Test creating observation from error MCP result."""
        # Create mock MCP result
        result = MagicMock(spec=mcp.types.CallToolResult)
        result.content = [mcp.types.TextContent(type="text", text="Operation failed")]
        result.isError = True
        result.structuredContent = None
        result.meta = None

        observation = MCPToolObservation.from_call_tool_result(
            tool_name="test_tool", result=result
        )

        assert observation.tool_name == "test_tool"
        assert observation.is_error is True
        assert len(observation.content) == 2
        assert isinstance(observation.content[0], TextContent)
        assert observation.content[0].text == "[Tool 'test_tool' executed.]"
        assert isinstance(observation.content[1], TextContent)
        assert observation.content[1].text == "Operation failed"

    def test_from_call_tool_result_with_image(self):
        """Test creating observation from MCP result with image content."""
        # Create mock MCP result with image
        result = MagicMock(spec=mcp.types.CallToolResult)
        result.content = [
            mcp.types.TextContent(type="text", text="Here's the image:"),
            mcp.types.ImageContent(
                type="image", data="base64data", mimeType="image/png"
            ),
        ]
        result.isError = False
        result.structuredContent = None
        result.meta = None

        observation = MCPToolObservation.from_call_tool_result(
            tool_name="test_tool", result=result
        )

        assert observation.tool_name == "test_tool"
        assert observation.content is not None
        assert len(observation.content) == 3
        # First item is header
        assert isinstance(observation.content[0], TextContent)
        assert observation.content[0].text == "[Tool 'test_tool' executed.]"
        # Second item is text
        assert isinstance(observation.content[1], TextContent)
        assert observation.content[1].text == "Here's the image:"
        # Third item is image
        assert isinstance(observation.content[2], ImageContent)
        assert hasattr(observation.content[2], "image_urls")
        assert observation.is_error is False

    def test_structured_result_fields_are_additive_and_not_llm_content(self):
        result = mcp.types.CallToolResult(
            _meta={"ui": {"resourceUri": "ui://app/index.html"}},
            structuredContent={"rows": [{"value": "visible-to-client"}]},
            content=[mcp.types.TextContent(type="text", text="model-visible")],
        )

        observation = MCPToolObservation.from_call_tool_result("test_tool", result)
        event = ObservationEvent(
            observation=observation,
            action_id="action-1",
            tool_name="test_tool",
            tool_call_id="call-1",
        )
        restored_event = Event.model_validate_json(event.model_dump_json())
        assert isinstance(restored_event, ObservationEvent)
        restored = restored_event.observation
        assert isinstance(restored, MCPToolObservation)

        assert restored.structured_content == result.structuredContent
        assert restored.result_meta == result.meta
        assert "visible-to-client" not in str(restored.to_llm_content)
        assert "model-visible" in str(restored.to_llm_content)

    def test_v1_44_1_mcp_observation_without_structured_result_fields(self):
        restored_event = Event.model_validate_json(V1_44_1_EVENT_JSON)

        assert isinstance(restored_event, ObservationEvent)
        observation = restored_event.observation
        assert isinstance(observation, MCPToolObservation)
        assert observation.structured_content is None
        assert observation.result_meta is None

    def test_to_llm_content_success(self):
        """Test agent observation formatting for success."""
        observation = MCPToolObservation.from_text(
            text="[Tool 'test_tool' executed.]\nSuccess result",
            tool_name="test_tool",
        )

        agent_obs = observation.to_llm_content
        assert len(agent_obs) == 1
        assert isinstance(agent_obs[0], TextContent)
        assert "[Tool 'test_tool' executed.]" in agent_obs[0].text
        assert "Success result" in agent_obs[0].text
        assert MCPToolObservation.ERROR_MESSAGE_HEADER not in agent_obs[0].text

    def test_to_llm_content_error(self):
        """Test agent observation formatting for error."""
        observation = MCPToolObservation.from_text(
            text=(
                "[Tool 'test_tool' executed.]\n"
                "[An error occurred during execution.]\n"
                "Error occurred"
            ),
            tool_name="test_tool",
            is_error=True,
        )

        agent_obs = observation.to_llm_content
        assert len(agent_obs) == 2
        assert isinstance(agent_obs[0], TextContent)
        assert agent_obs[0].text == MCPToolObservation.ERROR_MESSAGE_HEADER
        assert isinstance(agent_obs[1], TextContent)
        assert "[Tool 'test_tool' executed.]" in agent_obs[1].text
        assert "[An error occurred during execution.]" in agent_obs[1].text
        assert "Error occurred" in agent_obs[1].text


class TestMCPToolExecutor:
    """Test MCPToolExecutor functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client: Mock = MagicMock()
        self.executor: Any = MCPToolExecutor(
            tool_name="test_tool", client=self.mock_client
        )

    def test_call_tool_success(self):
        """Test successful tool execution."""
        # Mock successful MCP call
        mock_result = MagicMock(spec=mcp.types.CallToolResult)
        mock_result.content = [
            mcp.types.TextContent(type="text", text="Success result")
        ]
        mock_result.isError = False
        mock_result.structuredContent = None
        mock_result.meta = None

        # Mock action
        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"param": "value"}

        # Mock call_async_from_sync to return the expected observation
        def mock_call_async_from_sync(coro_func, **kwargs):
            return MCPToolObservation.from_call_tool_result(
                tool_name="test_tool", result=mock_result
            )

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        observation = self.executor(mock_action)

        assert isinstance(observation, MCPToolObservation)
        assert observation.tool_name == "test_tool"
        assert observation.is_error is False

    def test_call_tool_error(self):
        """Test tool execution with error."""
        # Mock error MCP call
        mock_result = MagicMock(spec=mcp.types.CallToolResult)
        mock_result.content = [
            mcp.types.TextContent(type="text", text="Error occurred")
        ]
        mock_result.isError = True
        mock_result.structuredContent = None
        mock_result.meta = None

        # Mock action
        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"param": "value"}

        # Mock call_async_from_sync to return the expected observation
        def mock_call_async_from_sync(coro_func, **kwargs):
            return MCPToolObservation.from_call_tool_result(
                tool_name="test_tool", result=mock_result
            )

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        observation = self.executor(mock_action)

        assert isinstance(observation, MCPToolObservation)
        assert observation.tool_name == "test_tool"
        assert observation.is_error is True

    def test_call_tool_exception(self):
        """Test tool execution with exception."""
        # Mock action
        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"param": "value"}

        # Mock call_async_from_sync to return an error observation
        def mock_call_async_from_sync(coro_func, **kwargs):
            return MCPToolObservation.from_text(
                text="Error calling MCP tool test_tool: Connection failed",
                tool_name="test_tool",
                is_error=True,
            )

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        observation = self.executor(mock_action)

        assert isinstance(observation, MCPToolObservation)
        assert observation.tool_name == "test_tool"
        assert observation.is_error is True
        assert observation.is_error is True
        assert "Connection failed" in observation.text

    def test_call_tool_timeout(self):
        """Test tool execution with timeout error returns observation."""
        # Mock action
        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"param": "value"}

        # Mock call_async_from_sync to raise TimeoutError
        def mock_call_async_from_sync(coro_func, **kwargs):
            raise TimeoutError("Operation timed out")

        self.mock_client.call_async_from_sync = mock_call_async_from_sync

        observation = self.executor(mock_action)

        assert isinstance(observation, MCPToolObservation)
        assert observation.tool_name == "test_tool"
        assert observation.is_error is True
        assert "timed out" in observation.text
        assert f"{self.executor.timeout} seconds" in observation.text

    def test_close_calls_client_sync_close(self):
        """close() must invoke MCPClient.sync_close() to tear down the
        stdio subprocess. Without this, MCP clients survive conversation
        deletion and accumulate over a long-running server."""
        self.executor.close()
        self.mock_client.sync_close.assert_called_once()

    def test_call_tool_reconnects_when_session_lost(self):
        """When is_connected() returns False but the client is not closed,
        call_tool should attempt reconnection before failing."""
        mock_result = MagicMock(spec=mcp.types.CallToolResult)
        mock_result.content = [
            mcp.types.TextContent(type="text", text="Success after reconnect")
        ]
        mock_result.isError = False
        mock_result.structuredContent = None
        mock_result.meta = None

        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"param": "value"}
        mock_action.to_mcp_arguments.return_value = {"param": "value"}

        # Use MockMCPClient (real AsyncExecutor) so call_tool actually runs
        client = MockMCPClient()
        connect_calls: list[int] = []

        async def mock_connect():
            connect_calls.append(1)

        async def mock_is_connected():
            return False if not connect_calls else True

        async def mock_call_tool_mcp(**kwargs):
            return mock_result

        # Patch the methods that fastmcp.Client.is_connected() and connect() use
        client.is_connected = lambda: len(connect_calls) > 0  # type: ignore[method-assign]
        client._closed = False
        client.connect = mock_connect  # type: ignore[method-assign]
        client.call_tool_mcp = mock_call_tool_mcp  # type: ignore[method-assign]

        executor = MCPToolExecutor(tool_name="test_tool", client=client)
        observation = executor(mock_action)

        assert isinstance(observation, MCPToolObservation)
        assert observation.tool_name == "test_tool"
        assert observation.is_error is False
        assert len(connect_calls) == 1
        client.sync_close()

    def test_call_tool_fails_when_client_closed(self):
        """When the client has been closed (_closed=True), call_tool should
        not attempt reconnection and should fail immediately."""
        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"param": "value"}
        mock_action.to_mcp_arguments.return_value = {"param": "value"}

        client = MockMCPClient()
        client.is_connected = lambda: False  # type: ignore[method-assign]
        client._closed = True

        async def mock_connect():
            raise AssertionError("connect() should not be called on a closed client")

        client.connect = mock_connect  # type: ignore[method-assign]

        async def mock_call_tool_mcp(**kwargs):
            raise AssertionError(
                "call_tool_mcp should not be called when client is closed"
            )

        client.call_tool_mcp = mock_call_tool_mcp  # type: ignore[method-assign]

        executor = MCPToolExecutor(tool_name="test_tool", client=client)
        observation = executor(mock_action)

        assert isinstance(observation, MCPToolObservation)
        assert observation.is_error is True
        assert "has been closed" in observation.text
        client.sync_close()

    def test_call_tool_fails_when_reconnect_fails(self):
        """When reconnection attempt fails, call_tool should return an error
        observation with the reconnection failure message."""
        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"param": "value"}
        mock_action.to_mcp_arguments.return_value = {"param": "value"}

        client = MockMCPClient()
        client.is_connected = lambda: False  # type: ignore[method-assign]
        client._closed = False

        async def mock_connect():
            raise RuntimeError("Server unreachable")

        client.connect = mock_connect  # type: ignore[method-assign]

        async def mock_call_tool_mcp(**kwargs):
            raise AssertionError(
                "call_tool_mcp should not be called when reconnect fails"
            )

        client.call_tool_mcp = mock_call_tool_mcp  # type: ignore[method-assign]

        executor = MCPToolExecutor(tool_name="test_tool", client=client)
        observation = executor(mock_action)

        assert isinstance(observation, MCPToolObservation)
        assert observation.is_error is True
        assert "Reconnection attempt failed" in observation.text
        assert "Server unreachable" in observation.text
        client.sync_close()


class TestMCPTool:
    """Test MCPTool functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client: MockMCPClient = MockMCPClient()

        # Create mock MCP tool
        self.mock_mcp_tool: Mock = MagicMock(spec=mcp.types.Tool)
        self.mock_mcp_tool.name = "test_tool"
        self.mock_mcp_tool.description = "A test tool"
        self.mock_mcp_tool.inputSchema = {
            "type": "object",
            "properties": {"param": {"type": "string"}},
        }
        self.mock_mcp_tool.annotations = None
        self.mock_mcp_tool.meta = None

        tools = MCPToolDefinition.create(
            mcp_tool=self.mock_mcp_tool, mcp_client=self.mock_client
        )
        self.tool: MCPToolDefinition = tools[0]  # Extract single tool from sequence

    def test_mcp_tool_creation(self):
        """Test creating an MCP tool."""
        assert self.tool.name == "test_tool"
        assert self.tool.description == "A test tool"

        # Get the schema from the OpenAI tool since MCPToolAction now uses dynamic
        # schema
        openai_tool = self.tool.to_openai_tool()
        function_def = openai_tool["function"]
        assert "parameters" in function_def
        input_schema = function_def["parameters"]

        # Since security_risk was removed from Action, it should not be in schema
        # Summary field is always added for LLM transparency
        assert len(input_schema["properties"]) == 2
        assert "security_risk" not in input_schema["properties"]
        assert "summary" in input_schema["properties"]

        # Check the actual tool parameter is present
        assert "param" in input_schema["properties"]
        assert input_schema["properties"]["param"] == {"type": "string"}

    def test_mcp_tool_with_annotations(self):
        """Test creating an MCP tool with annotations."""
        # Mock tool with annotations
        mock_tool_with_annotations = MagicMock(spec=mcp.types.Tool)
        mock_tool_with_annotations.name = "annotated_tool"
        mock_tool_with_annotations.description = "Tool with annotations"
        mock_tool_with_annotations.inputSchema = {"type": "object"}
        mock_tool_with_annotations.annotations = ToolAnnotations(title="Annotated Tool")
        mock_tool_with_annotations.meta = {"version": "1.0"}

        tools = MCPToolDefinition.create(
            mcp_tool=mock_tool_with_annotations, mcp_client=self.mock_client
        )
        tool = tools[0]  # Extract single tool from sequence

        assert tool.name == "annotated_tool"
        assert tool.description == "Tool with annotations"
        assert tool.annotations is not None

    def test_mcp_tool_no_description(self):
        """Test creating an MCP tool without description."""
        # Mock tool without description
        mock_tool_no_desc = MagicMock(spec=mcp.types.Tool)
        mock_tool_no_desc.name = "no_desc_tool"
        mock_tool_no_desc.description = None
        mock_tool_no_desc.inputSchema = {"type": "object"}
        mock_tool_no_desc.annotations = None
        mock_tool_no_desc.meta = None

        tools = MCPToolDefinition.create(
            mcp_tool=mock_tool_no_desc, mcp_client=self.mock_client
        )
        tool = tools[0]  # Extract single tool from sequence

        assert tool.name == "no_desc_tool"
        assert tool.description == "No description provided"

    def test_executor_assignment(self):
        """Test that the tool has the correct executor."""
        assert isinstance(self.tool.executor, MCPToolExecutor)
        assert self.tool.executor.tool_name == "test_tool"
        assert self.tool.executor.client == self.mock_client


def test_action_type_cache_is_bounded():
    """A tool whose schema keeps changing must not grow the cache forever."""
    from openhands.sdk.mcp.tool import (
        _MCP_ACTION_TYPE_CACHE_MAX,
        _create_mcp_action_type,
        _mcp_dynamic_action_type,
    )

    for i in range(_MCP_ACTION_TYPE_CACHE_MAX + 50):
        tool = mcp.types.Tool(
            name="churning_tool",
            description="d",
            inputSchema={
                "type": "object",
                "properties": {f"field_{i}": {"type": "string"}},
            },
        )
        _create_mcp_action_type(tool)

    assert len(_mcp_dynamic_action_type) <= _MCP_ACTION_TYPE_CACHE_MAX


def test_action_type_cache_serializes_get_and_evict(monkeypatch):
    """A cache hit must not observe a concurrent eviction of the same key.

    Forces the exact interleaving a real race could produce: pause inside
    the cache-hit path (after `.get()`, before `.move_to_end()`) and let a
    second thread try to evict that same entry. Without the lock this
    raises KeyError from `move_to_end`; with it, the second thread blocks
    until the first thread's critical section completes.
    """
    import threading
    import time
    from collections import OrderedDict

    import openhands.sdk.mcp.tool as tool_module

    paused = threading.Event()

    class PausingDict(OrderedDict):
        def get(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            result = super().get(*args, **kwargs)
            if result is not None and not paused.is_set():
                paused.set()
                time.sleep(0.3)
            return result

    monkeypatch.setattr(tool_module, "_MCP_ACTION_TYPE_CACHE_MAX", 1)
    monkeypatch.setattr(tool_module, "_mcp_dynamic_action_type", PausingDict())

    shared_tool = mcp.types.Tool(
        name="shared", description="d", inputSchema={"type": "object"}
    )
    other_tool = mcp.types.Tool(
        name="other", description="d", inputSchema={"type": "object"}
    )
    tool_module._create_mcp_action_type(shared_tool)  # seed the cache

    errors: list[Exception] = []

    def hit():
        try:
            tool_module._create_mcp_action_type(shared_tool)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def evict():
        paused.wait(2.0)
        try:
            tool_module._create_mcp_action_type(other_tool)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=hit), threading.Thread(target=evict)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)

    assert not errors
