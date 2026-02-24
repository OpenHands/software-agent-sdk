"""Tests for ACPAgent."""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.sdk.agent.acp_agent import ACPAgent, _OpenHandsACPBridge
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ACPToolCallEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.workspace.local import LocalWorkspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(**kwargs) -> ACPAgent:
    return ACPAgent(acp_command=["echo", "test"], **kwargs)


def _make_state(tmp_path) -> ConversationState:
    agent = _make_agent()
    workspace = LocalWorkspace(working_dir=str(tmp_path))
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=workspace,
    )


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestACPAgentInstantiation:
    def test_creates_with_sentinel_llm(self):
        agent = _make_agent()
        assert agent.llm.model == "acp-managed"

    def test_creates_with_empty_tools(self):
        agent = _make_agent()
        assert agent.tools == []

    def test_creates_with_empty_default_tools(self):
        agent = _make_agent()
        assert agent.include_default_tools == []

    def test_requires_acp_command(self):
        with pytest.raises(Exception):
            ACPAgent()  # type: ignore[call-arg]

    def test_acp_command_stored(self):
        agent = ACPAgent(acp_command=["npx", "-y", "claude-code-acp"])
        assert agent.acp_command == ["npx", "-y", "claude-code-acp"]

    def test_acp_args_default_empty(self):
        agent = _make_agent()
        assert agent.acp_args == []

    def test_acp_env_default_empty(self):
        agent = _make_agent()
        assert agent.acp_env == {}

    def test_system_message_returns_acp_managed(self):
        agent = _make_agent()
        assert agent.system_message == "ACP-managed agent"

    def test_get_all_llms_yields_sentinel(self):
        agent = _make_agent()
        llms = list(agent.get_all_llms())
        assert len(llms) == 1
        assert llms[0].model == "acp-managed"

    def test_agent_is_frozen(self):
        agent = _make_agent()
        with pytest.raises(Exception):
            agent.acp_command = ["other"]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestACPAgentSerialization:
    def test_kind_is_acp_agent(self):
        agent = _make_agent()
        data = json.loads(agent.model_dump_json())
        assert data["kind"] == "ACPAgent"

    def test_roundtrip_serialization(self):
        agent = ACPAgent(
            acp_command=["npx", "-y", "claude-code-acp"],
            acp_args=["--verbose"],
            acp_env={"FOO": "bar"},
        )
        dumped = agent.model_dump_json()
        restored = AgentBase.model_validate_json(dumped)
        assert isinstance(restored, ACPAgent)
        assert restored.acp_command == agent.acp_command
        assert restored.acp_args == agent.acp_args
        assert restored.acp_env == agent.acp_env

    def test_deserialization_from_dict(self):
        data = {
            "kind": "ACPAgent",
            "acp_command": ["echo", "test"],
        }
        agent = AgentBase.model_validate(data)
        assert isinstance(agent, ACPAgent)
        assert agent.acp_command == ["echo", "test"]


# ---------------------------------------------------------------------------
# Feature validation (init_state guards)
# ---------------------------------------------------------------------------


class TestACPAgentValidation:
    """Test that unsupported features raise NotImplementedError in init_state."""

    def _init_with_patches(self, agent, tmp_path):
        """Call init_state with ACP SDK mocked out."""
        state = _make_state(tmp_path)
        events = []
        with (
            patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"),
            patch(
                "openhands.sdk.utils.async_executor.AsyncExecutor",
                return_value=MagicMock(),
            ),
        ):
            agent.init_state(state, on_event=events.append)
        return events

    def test_rejects_mcp_config(self, tmp_path):
        agent = ACPAgent(
            acp_command=["echo"],
            mcp_config={"mcpServers": {"test": {"command": "echo"}}},
        )
        with pytest.raises(NotImplementedError, match="mcp_config"):
            self._init_with_patches(agent, tmp_path)


# ---------------------------------------------------------------------------
# init_state
# ---------------------------------------------------------------------------


class TestACPAgentInitState:
    def test_init_state_emits_system_prompt_placeholder(self, tmp_path):
        agent = _make_agent()
        state = _make_state(tmp_path)
        events: list = []

        with (
            patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"),
        ):
            agent.init_state(state, on_event=events.append)

        assert len(events) == 1
        assert isinstance(events[0], SystemPromptEvent)
        assert "ACP server" in events[0].system_prompt.text
        assert events[0].tools == []


# ---------------------------------------------------------------------------
# _OpenHandsACPBridge
# ---------------------------------------------------------------------------


class TestOpenHandsACPClient:
    def test_reset_clears_state(self):
        client = _OpenHandsACPBridge()
        client.accumulated_text.append("hello")
        client.accumulated_thoughts.append("thinking")
        client.on_token = lambda _: None

        client.reset()

        assert client.accumulated_text == []
        assert client.accumulated_thoughts == []
        assert client.on_token is None

    @pytest.mark.asyncio
    async def test_session_update_accumulates_text(self):
        client = _OpenHandsACPBridge()
        client.accumulated_text.append("Hello")
        client.accumulated_text.append(" World")
        assert "".join(client.accumulated_text) == "Hello World"

    @pytest.mark.asyncio
    async def test_session_update_accumulates_thoughts(self):
        client = _OpenHandsACPBridge()
        client.accumulated_thoughts.append("Let me think")
        client.accumulated_thoughts.append(" about this")
        assert "".join(client.accumulated_thoughts) == "Let me think about this"

    def test_on_token_callback(self):
        client = _OpenHandsACPBridge()
        tokens: list[str] = []
        client.on_token = tokens.append

        # Simulate what session_update would do
        text = "chunk1"
        client.accumulated_text.append(text)
        if client.on_token is not None:
            client.on_token(text)

        assert tokens == ["chunk1"]

    @pytest.mark.asyncio
    async def test_fs_methods_raise(self):
        client = _OpenHandsACPBridge()
        with pytest.raises(NotImplementedError):
            await client.write_text_file("c", "/f", "s1")
        with pytest.raises(NotImplementedError):
            await client.read_text_file("/f", "s1")

    @pytest.mark.asyncio
    async def test_terminal_methods_raise(self):
        client = _OpenHandsACPBridge()
        with pytest.raises(NotImplementedError):
            await client.create_terminal("bash", "s1")
        with pytest.raises(NotImplementedError):
            await client.terminal_output("s1", "t1")
        with pytest.raises(NotImplementedError):
            await client.release_terminal("s1", "t1")
        with pytest.raises(NotImplementedError):
            await client.wait_for_terminal_exit("s1", "t1")
        with pytest.raises(NotImplementedError):
            await client.kill_terminal("s1", "t1")

    @pytest.mark.asyncio
    async def test_ext_method_returns_empty_dict(self):
        client = _OpenHandsACPBridge()
        result = await client.ext_method("test", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_ext_notification_is_noop(self):
        client = _OpenHandsACPBridge()
        await client.ext_notification("test", {})  # Should not raise


# ---------------------------------------------------------------------------
# step
# ---------------------------------------------------------------------------


class TestACPAgentStep:
    def _make_conversation_with_message(self, tmp_path, text="Hello"):
        """Create a mock conversation with a user message."""
        state = _make_state(tmp_path)
        state.events.append(
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="ACP-managed agent"),
                tools=[],
            )
        )
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text=text)]),
            )
        )

        conversation = MagicMock()
        conversation.state = state
        return conversation

    def test_step_emits_message_event(self, tmp_path):
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        # Set up mocked runtime state — populate text *after* reset
        # (step() calls client.reset() then run_async which populates text)
        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("The answer is 4")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        # step() emits MessageEvent + ActionEvent(FinishAction) + ObservationEvent(FinishObservation)
        assert len(events) == 3
        assert isinstance(events[0], MessageEvent)
        assert events[0].source == "agent"
        content_block = events[0].llm_message.content[0]
        assert isinstance(content_block, TextContent)
        assert content_block.text == "The answer is 4"

    def test_step_includes_reasoning(self, tmp_path):
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("4")
            mock_client.accumulated_thoughts.append("I need to add 2+2")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        msg = events[0].llm_message
        assert msg.reasoning_content == "I need to add 2+2"

    def test_step_sets_finished(self, tmp_path):
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("done")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=lambda _: None)

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )

    def test_step_no_user_message_finishes(self, tmp_path):
        agent = _make_agent()
        state = _make_state(tmp_path)
        # No user message added

        conversation = MagicMock()
        conversation.state = state

        agent._client = _OpenHandsACPBridge()

        agent.step(conversation, on_event=lambda _: None)

        assert state.execution_status == ConversationExecutionStatus.FINISHED

    def test_step_error_sets_error_status(self, tmp_path):
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        mock_executor = MagicMock()
        mock_executor.run_async = MagicMock(side_effect=RuntimeError("boom"))
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
        assert len(events) == 1
        content_block = events[0].llm_message.content[0]
        assert isinstance(content_block, TextContent)
        assert "ACP error: boom" in content_block.text

    def test_step_no_response_text_fallback(self, tmp_path):
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        # accumulated_text stays empty — run_async is a no-op
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        mock_executor = MagicMock()
        mock_executor.run_async = lambda _coro, **_kwargs: None
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        content_block = events[0].llm_message.content[0]
        assert isinstance(content_block, TextContent)
        assert "(No response from ACP server)" in content_block.text

    def test_step_passes_on_token(self, tmp_path):
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("ok")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        on_token = MagicMock()

        agent.step(conversation, on_event=lambda _: None, on_token=on_token)

        # Verify on_token was passed to the client
        assert mock_client.on_token == on_token


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestACPAgentCleanup:
    def test_close_terminates_process(self):
        agent = _make_agent()
        mock_process = MagicMock()
        agent._process = mock_process
        agent._executor = MagicMock()
        agent._conn = None

        agent.close()

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    def test_close_is_idempotent(self):
        agent = _make_agent()
        mock_process = MagicMock()
        agent._process = mock_process
        agent._executor = MagicMock()
        agent._conn = None

        agent.close()
        agent.close()  # Second call should be a no-op

        # terminate/kill should only be called once
        mock_process.terminate.assert_called_once()

    def test_close_closes_executor(self):
        agent = _make_agent()
        mock_executor = MagicMock()
        agent._executor = mock_executor
        agent._process = None
        agent._conn = None

        agent.close()

        mock_executor.close.assert_called_once()

    def test_close_handles_errors_gracefully(self):
        agent = _make_agent()
        mock_process = MagicMock()
        mock_process.terminate.side_effect = OSError("already dead")
        mock_process.kill.side_effect = OSError("already dead")
        agent._process = mock_process
        agent._executor = MagicMock()
        agent._conn = None

        # Should not raise
        agent.close()


# ---------------------------------------------------------------------------
# _filter_jsonrpc_lines
# ---------------------------------------------------------------------------


class TestFilterJsonrpcLines:
    @pytest.mark.asyncio
    async def test_passes_jsonrpc_lines(self):
        from openhands.sdk.agent.acp_agent import _filter_jsonrpc_lines

        source = asyncio.StreamReader()
        dest = asyncio.StreamReader()

        jsonrpc_line = b'{"jsonrpc":"2.0","method":"test"}\n'
        source.feed_data(jsonrpc_line)
        source.feed_eof()

        await _filter_jsonrpc_lines(source, dest)

        result = await dest.readline()
        assert result == jsonrpc_line

    @pytest.mark.asyncio
    async def test_filters_non_jsonrpc_lines(self):
        from openhands.sdk.agent.acp_agent import _filter_jsonrpc_lines

        source = asyncio.StreamReader()
        dest = asyncio.StreamReader()

        source.feed_data(b"[ACP] Starting server...\n")
        source.feed_data(b'{"jsonrpc":"2.0","id":1}\n')
        source.feed_data(b"Some debug output\n")
        source.feed_eof()

        await _filter_jsonrpc_lines(source, dest)

        result = await dest.readline()
        assert b'"jsonrpc"' in result

        # Should get EOF next (non-JSON lines were filtered)
        result2 = await dest.readline()
        assert result2 == b""

    @pytest.mark.asyncio
    async def test_filters_pretty_printed_json(self):
        from openhands.sdk.agent.acp_agent import _filter_jsonrpc_lines

        source = asyncio.StreamReader()
        dest = asyncio.StreamReader()

        # Pretty-printed JSON starts with { but doesn't contain "jsonrpc"
        source.feed_data(b"{\n")
        source.feed_data(b'  "type": "message"\n')
        source.feed_data(b"}\n")
        source.feed_eof()

        await _filter_jsonrpc_lines(source, dest)

        # Should only get EOF
        result = await dest.readline()
        assert result == b""


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class TestACPAgentTelemetry:
    def _make_conversation_with_message(self, tmp_path, text="Hello"):
        """Create a mock conversation with a user message."""
        state = _make_state(tmp_path)
        state.events.append(
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="ACP-managed agent"),
                tools=[],
            )
        )
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text=text)]),
            )
        )

        conversation = MagicMock()
        conversation.state = state
        return conversation

    def test_get_all_llms_yields_sentinel(self):
        """get_all_llms() yields the sentinel LLM for telemetry."""
        agent = _make_agent()
        llms = list(agent.get_all_llms())
        assert len(llms) == 1
        assert llms[0] is agent.llm
        assert llms[0].model == "acp-managed"

    def test_step_records_token_usage(self, tmp_path):
        """step() records per-turn token usage from PromptResponse.usage."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        mock_client._context_window = 200000
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        # Build a mock PromptResponse with usage
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.cached_read_tokens = 10
        mock_usage.cached_write_tokens = 5
        mock_usage.thought_tokens = 20

        mock_response = MagicMock()
        mock_response.usage = mock_usage

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("response text")
            return mock_response

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=lambda _: None)

        # Verify token usage was recorded
        metrics = agent.llm.metrics
        assert len(metrics.token_usages) == 1
        usage = metrics.token_usages[0]
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.cache_read_tokens == 10
        assert usage.cache_write_tokens == 5
        assert usage.reasoning_tokens == 20
        assert usage.context_window == 200000

    def test_step_handles_no_usage(self, tmp_path):
        """step() handles PromptResponse with no usage gracefully."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        mock_response = MagicMock()
        mock_response.usage = None

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("response")
            return mock_response

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=lambda _: None)

        # No token usage should be recorded
        assert len(agent.llm.metrics.token_usages) == 0

    @pytest.mark.asyncio
    async def test_usage_update_records_cost(self):
        """UsageUpdate with cost records incremental cost via metrics."""
        from acp.schema import UsageUpdate

        from openhands.sdk.llm import LLM

        client = _OpenHandsACPBridge()
        llm = LLM(model="acp-managed")
        client._llm_ref = llm
        client._last_cost = 0.0

        update = MagicMock(spec=UsageUpdate)
        update.size = 128000
        update.cost = MagicMock()
        update.cost.amount = 0.05

        await client.session_update("sess-1", update)

        assert llm.metrics.accumulated_cost == pytest.approx(0.05)
        assert client._last_cost == 0.05
        assert client._context_window == 128000

    @pytest.mark.asyncio
    async def test_usage_update_incremental_cost(self):
        """UsageUpdate cost tracking is incremental (delta from last seen)."""
        from acp.schema import UsageUpdate

        from openhands.sdk.llm import LLM

        client = _OpenHandsACPBridge()
        llm = LLM(model="acp-managed")
        client._llm_ref = llm

        # First update: cost 0.05
        update1 = MagicMock(spec=UsageUpdate)
        update1.size = 128000
        update1.cost = MagicMock()
        update1.cost.amount = 0.05

        await client.session_update("sess-1", update1)
        assert llm.metrics.accumulated_cost == pytest.approx(0.05)

        # Second update: cumulative cost 0.12 → delta should be 0.07
        update2 = MagicMock(spec=UsageUpdate)
        update2.size = 130000
        update2.cost = MagicMock()
        update2.cost.amount = 0.12

        await client.session_update("sess-1", update2)
        assert llm.metrics.accumulated_cost == pytest.approx(0.12)
        assert client._last_cost == 0.12

    @pytest.mark.asyncio
    async def test_usage_update_updates_context_window(self):
        """UsageUpdate.size updates the client's _context_window."""
        from acp.schema import UsageUpdate

        client = _OpenHandsACPBridge()

        update = MagicMock(spec=UsageUpdate)
        update.size = 200000
        update.cost = None

        await client.session_update("sess-1", update)

        assert client._context_window == 200000

    def test_stats_callback_invoked(self, tmp_path):
        """After step(), the sentinel LLM's stats callback is invoked."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        mock_response = MagicMock()
        mock_response.usage = None

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("ok")
            return mock_response

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        # Set up a stats callback
        callback = MagicMock()
        agent.llm.telemetry._stats_update_callback = callback

        agent.step(conversation, on_event=lambda _: None)

        callback.assert_called_once()

    def test_start_acp_server_wires_llm_ref(self, tmp_path):
        """_start_acp_server wires _llm_ref on the client."""
        agent = _make_agent()
        state = _make_state(tmp_path)

        with patch(
            "openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"
        ) as mock_start:

            def fake_start(s):
                client = _OpenHandsACPBridge()
                client._llm_ref = agent.llm
                agent._client = client

            mock_start.side_effect = fake_start
            agent.init_state(state, on_event=lambda _: None)

        assert agent._client._llm_ref is agent.llm

    def test_reset_preserves_telemetry_state(self):
        """reset() clears text/thoughts but preserves telemetry state."""
        client = _OpenHandsACPBridge()
        client._last_cost = 1.23
        client._context_window = 128000
        client._llm_ref = MagicMock()
        client.accumulated_text.append("hello")
        client.accumulated_thoughts.append("thinking")

        client.reset()

        assert client.accumulated_text == []
        assert client.accumulated_thoughts == []
        assert client._last_cost == 1.23
        assert client._context_window == 128000
        assert client._llm_ref is not None


# ---------------------------------------------------------------------------
# Tool call accumulation and emission
# ---------------------------------------------------------------------------


class TestACPToolCallAccumulation:
    """Tests for ToolCallStart/ToolCallProgress accumulation in the bridge."""

    @pytest.mark.asyncio
    async def test_session_update_accumulates_tool_call_start(self):
        """ToolCallStart creates an entry in accumulated_tool_calls."""
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Read file"
        start.kind = "read"
        start.status = "in_progress"
        start.raw_input = {"path": "/tmp/test.py"}
        start.raw_output = None

        await client.session_update("sess-1", start)

        assert len(client.accumulated_tool_calls) == 1
        tc = client.accumulated_tool_calls[0]
        assert tc["tool_call_id"] == "tc-1"
        assert tc["title"] == "Read file"
        assert tc["tool_kind"] == "read"
        assert tc["status"] == "in_progress"
        assert tc["raw_input"] == {"path": "/tmp/test.py"}
        assert tc["raw_output"] is None

    @pytest.mark.asyncio
    async def test_session_update_merges_tool_call_progress(self):
        """ToolCallProgress merges updates into the existing tool call entry."""
        from acp.schema import ToolCallProgress, ToolCallStart

        client = _OpenHandsACPBridge()

        # Start
        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-2"
        start.title = "Execute command"
        start.kind = "execute"
        start.status = "in_progress"
        start.raw_input = {"command": "ls"}
        start.raw_output = None

        await client.session_update("sess-1", start)

        # Progress
        progress = MagicMock(spec=ToolCallProgress)
        progress.tool_call_id = "tc-2"
        progress.title = None  # not updated
        progress.kind = None  # not updated
        progress.status = "completed"
        progress.raw_input = None  # not updated
        progress.raw_output = "file1.py\nfile2.py"

        await client.session_update("sess-1", progress)

        assert len(client.accumulated_tool_calls) == 1
        tc = client.accumulated_tool_calls[0]
        assert tc["title"] == "Execute command"  # unchanged
        assert tc["tool_kind"] == "execute"  # unchanged
        assert tc["status"] == "completed"  # updated
        assert tc["raw_output"] == "file1.py\nfile2.py"  # updated

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_accumulated(self):
        """Multiple ToolCallStart events create separate entries."""
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()

        for i in range(3):
            start = MagicMock(spec=ToolCallStart)
            start.tool_call_id = f"tc-{i}"
            start.title = f"Tool {i}"
            start.kind = "read"
            start.status = "completed"
            start.raw_input = None
            start.raw_output = None
            await client.session_update("sess-1", start)

        assert len(client.accumulated_tool_calls) == 3
        assert [tc["tool_call_id"] for tc in client.accumulated_tool_calls] == [
            "tc-0",
            "tc-1",
            "tc-2",
        ]

    def test_reset_clears_accumulated_tool_calls(self):
        """reset() clears accumulated_tool_calls."""
        client = _OpenHandsACPBridge()
        client.accumulated_tool_calls.append(
            {
                "tool_call_id": "tc-1",
                "title": "Read file",
                "tool_kind": "read",
                "status": "completed",
                "raw_input": None,
                "raw_output": None,
            }
        )

        client.reset()

        assert client.accumulated_tool_calls == []


class TestACPToolCallEmission:
    """Tests for ACPToolCallEvent emission in step()."""

    def _make_conversation_with_message(self, tmp_path, text="Hello"):
        """Create a mock conversation with a user message."""
        state = _make_state(tmp_path)
        state.events.append(
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="ACP-managed agent"),
                tools=[],
            )
        )
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text=text)]),
            )
        )

        conversation = MagicMock()
        conversation.state = state
        return conversation

    def test_step_emits_tool_call_events_before_message(self, tmp_path):
        """step() emits ACPToolCallEvent for each tool call before the MessageEvent."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("done")
            mock_client.accumulated_tool_calls.extend(
                [
                    {
                        "tool_call_id": "tc-1",
                        "title": "Read file",
                        "tool_kind": "read",
                        "status": "completed",
                        "raw_input": {"path": "/tmp/f.py"},
                        "raw_output": "content",
                    },
                    {
                        "tool_call_id": "tc-2",
                        "title": "Execute bash",
                        "tool_kind": "execute",
                        "status": "failed",
                        "raw_input": {"command": "ls"},
                        "raw_output": None,
                    },
                ]
            )

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        # Should be: 2 tool call events + 1 message event + finish action + finish observation
        assert len(events) == 5
        assert isinstance(events[0], ACPToolCallEvent)
        assert isinstance(events[1], ACPToolCallEvent)
        assert isinstance(events[2], MessageEvent)

        # Verify first tool call event
        assert events[0].tool_call_id == "tc-1"
        assert events[0].title == "Read file"
        assert events[0].tool_kind == "read"
        assert events[0].status == "completed"
        assert events[0].raw_input == {"path": "/tmp/f.py"}
        assert events[0].raw_output == "content"
        assert events[0].is_error is False

        # Verify second tool call event (failed)
        assert events[1].tool_call_id == "tc-2"
        assert events[1].is_error is True

    def test_step_emits_no_tool_call_events_when_none(self, tmp_path):
        """step() emits only MessageEvent when no tool calls accumulated."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("no tools used")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        # MessageEvent + ActionEvent(FinishAction) + ObservationEvent(FinishObservation)
        assert len(events) == 3
        assert isinstance(events[0], MessageEvent)

    def test_tool_call_events_cleared_between_turns(self, tmp_path):
        """accumulated_tool_calls are cleared on reset() between turns."""
        agent = _make_agent()
        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        # Simulate first turn with tool calls
        mock_client.accumulated_tool_calls.append(
            {
                "tool_call_id": "tc-old",
                "title": "Old tool",
                "tool_kind": "read",
                "status": "completed",
                "raw_input": None,
                "raw_output": None,
            }
        )

        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        def _fake_run_async(_coro, **_kwargs):
            # After reset, accumulated_tool_calls should be empty
            # Only add text so step() succeeds
            mock_client.accumulated_text.append("response")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        # step() calls reset() which should clear old tool calls
        agent.step(conversation, on_event=events.append)

        # Only the MessageEvent + FinishAction + FinishObservation should appear —
        # the old tool call was cleared by reset()
        assert len(events) == 3
        assert isinstance(events[0], MessageEvent)


# ---------------------------------------------------------------------------
# ask_agent
# ---------------------------------------------------------------------------


class TestACPAgentAskAgent:
    def test_ask_agent_raises_if_not_initialized(self):
        """ask_agent() raises RuntimeError when _conn is None."""
        agent = _make_agent()
        # _conn and _session_id are None by default
        with pytest.raises(RuntimeError, match="no ACP connection"):
            agent.ask_agent("What is 2+2?")

    def test_ask_agent_raises_if_session_id_missing(self):
        """ask_agent() raises RuntimeError when _session_id is None."""
        agent = _make_agent()
        agent._conn = MagicMock()
        agent._session_id = None
        with pytest.raises(RuntimeError, match="no session ID"):
            agent.ask_agent("What is 2+2?")

    def test_ask_agent_forks_and_prompts(self):
        """ask_agent() forks the session, prompts, and returns the response."""
        agent = _make_agent()
        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "main-session"
        agent._working_dir = "/workspace"

        # Mock fork_session response
        mock_fork_response = MagicMock()
        mock_fork_response.session_id = "fork-session-123"

        # Mock prompt response (no usage)
        mock_prompt_response = MagicMock()
        mock_prompt_response.usage = None

        async def _fake_prompt(*args, **kwargs):
            # Simulate text arriving via session_update during prompt
            mock_client._fork_accumulated_text.extend(["Hello", " world"])
            return mock_prompt_response

        def _fake_run_async(coro_fn, **_kwargs):
            """Simulate the async execution synchronously."""
            loop = asyncio.new_event_loop()
            try:
                agent._conn.fork_session = AsyncMock(return_value=mock_fork_response)
                agent._conn.prompt = _fake_prompt
                return loop.run_until_complete(coro_fn())
            finally:
                loop.close()

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        result = agent.ask_agent("What is 2+2?")

        assert result == "Hello world"

    def test_ask_agent_records_token_usage(self):
        """ask_agent() records token usage from the PromptResponse."""
        agent = _make_agent()
        mock_client = _OpenHandsACPBridge()
        mock_client._context_window = 200000
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "main-session"
        agent._working_dir = "/workspace"

        mock_fork_response = MagicMock()
        mock_fork_response.session_id = "fork-session-456"

        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.cached_read_tokens = 10
        mock_usage.cached_write_tokens = 5
        mock_usage.thought_tokens = 20

        mock_prompt_response = MagicMock()
        mock_prompt_response.usage = mock_usage

        async def _fake_prompt(*args, **kwargs):
            mock_client._fork_accumulated_text.append("response")
            return mock_prompt_response

        def _fake_run_async(coro_fn, **_kwargs):
            loop = asyncio.new_event_loop()
            try:
                agent._conn.fork_session = AsyncMock(return_value=mock_fork_response)
                agent._conn.prompt = _fake_prompt
                return loop.run_until_complete(coro_fn())
            finally:
                loop.close()

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.ask_agent("Summarize this")

        metrics = agent.llm.metrics
        assert len(metrics.token_usages) == 1
        usage = metrics.token_usages[0]
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.cache_read_tokens == 10
        assert usage.cache_write_tokens == 5
        assert usage.reasoning_tokens == 20
        assert usage.context_window == 200000

    def test_ask_agent_cleans_up_fork_state(self):
        """ask_agent() cleans up fork state even on success."""
        agent = _make_agent()
        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "main-session"
        agent._working_dir = "/workspace"

        mock_fork_response = MagicMock()
        mock_fork_response.session_id = "fork-session-789"

        mock_prompt_response = MagicMock()
        mock_prompt_response.usage = None

        async def _fake_prompt(*args, **kwargs):
            mock_client._fork_accumulated_text.append("ok")
            return mock_prompt_response

        def _fake_run_async(coro_fn, **_kwargs):
            loop = asyncio.new_event_loop()
            try:
                agent._conn.fork_session = AsyncMock(return_value=mock_fork_response)
                agent._conn.prompt = _fake_prompt
                return loop.run_until_complete(coro_fn())
            finally:
                loop.close()

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.ask_agent("test")

        # Fork state should be cleaned up
        assert mock_client._fork_session_id is None
        assert mock_client._fork_accumulated_text == []


# ---------------------------------------------------------------------------
# Client fork text routing
# ---------------------------------------------------------------------------


class TestClientForkTextRouting:
    @pytest.mark.asyncio
    async def test_fork_text_routed_to_fork_accumulator(self):
        """When _fork_session_id is set, matching text goes to fork accumulator."""
        from acp.schema import AgentMessageChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        client._fork_session_id = "fork-sess"
        client._fork_accumulated_text = []

        update = MagicMock(spec=AgentMessageChunk)
        update.content = MagicMock(spec=TextContentBlock)
        update.content.text = "fork response"

        await client.session_update("fork-sess", update)

        assert client._fork_accumulated_text == ["fork response"]
        # Main accumulator should be empty
        assert client.accumulated_text == []

    @pytest.mark.asyncio
    async def test_main_text_unaffected_by_active_fork(self):
        """Main session text routes to accumulated_text even when fork is active."""
        from acp.schema import AgentMessageChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        client._fork_session_id = "fork-sess"
        client._fork_accumulated_text = []

        update = MagicMock(spec=AgentMessageChunk)
        update.content = MagicMock(spec=TextContentBlock)
        update.content.text = "main response"

        await client.session_update("main-sess", update)

        assert client.accumulated_text == ["main response"]
        assert client._fork_accumulated_text == []

    @pytest.mark.asyncio
    async def test_no_fork_normal_routing(self):
        """When _fork_session_id is None, all text goes to main accumulator."""
        from acp.schema import AgentMessageChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        assert client._fork_session_id is None

        update = MagicMock(spec=AgentMessageChunk)
        update.content = MagicMock(spec=TextContentBlock)
        update.content.text = "normal text"

        await client.session_update("any-session", update)

        assert client.accumulated_text == ["normal text"]
        assert client._fork_accumulated_text == []
