"""Tests for ACPAgent."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp.exceptions import RequestError as ACPRequestError

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    _build_session_meta,
    _estimate_cost_from_tokens,
    _extract_token_usage,
    _maybe_set_session_model,
    _OpenHandsACPBridge,
    _resolve_bypass_mode,
    _select_auth_method,
    _serialize_tool_content,
)
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ACPToolCallEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.skills import KeywordTrigger, Skill
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
        agent = ACPAgent(acp_command=["npx", "-y", "claude-agent-acp"])
        assert agent.acp_command == ["npx", "-y", "claude-agent-acp"]

    def test_acp_args_default_empty(self):
        agent = _make_agent()
        assert agent.acp_args == []

    def test_acp_env_default_empty(self):
        agent = _make_agent()
        assert agent.acp_env == {}

    def test_get_all_llms_yields_sentinel(self):
        agent = _make_agent()
        llms = list(agent.get_all_llms())
        assert len(llms) == 1
        assert llms[0].model == "acp-managed"

    def test_agent_is_frozen(self):
        agent = _make_agent()
        with pytest.raises(Exception):
            agent.acp_command = ["other"]  # type: ignore[misc]

    def test_acp_model_propagated_to_metrics(self):
        """When acp_model is set, metrics.model_name should reflect the actual model."""
        agent = _make_agent(acp_model="gemini-3-flash-preview")
        assert agent.llm.metrics.model_name == "gemini-3-flash-preview"
        assert agent.llm.metrics.accumulated_token_usage is not None
        assert (
            agent.llm.metrics.accumulated_token_usage.model == "gemini-3-flash-preview"
        )

    def test_acp_model_propagated_to_llm_model(self):
        """acp_model overrides the sentinel model name so logs/state show
        the real model. The ACP-sentinel marker lives on usage_id."""
        agent = _make_agent(acp_model="claude-opus-4-6")
        assert agent.llm.model == "claude-opus-4-6"
        assert agent.llm.usage_id == "acp-managed"

    def test_sentinel_usage_id_without_acp_model(self):
        agent = _make_agent()
        assert agent.llm.model == "acp-managed"
        assert agent.llm.usage_id == "acp-managed"

    def test_no_acp_model_keeps_sentinel(self):
        """Without acp_model, metrics.model_name remains the sentinel value."""
        agent = _make_agent()
        assert agent.llm.metrics.model_name == "acp-managed"

    def test_acp_model_used_in_cost_entries(self):
        """Cost entries should use the actual model name, not the sentinel."""
        agent = _make_agent(acp_model="claude-opus-4-6")
        agent.llm.metrics.add_cost(0.05)
        assert agent.llm.metrics.costs[0].model == "claude-opus-4-6"


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
            acp_command=["npx", "-y", "claude-agent-acp"],
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

    def test_allows_agent_context_for_prompt_extensions(self, tmp_path):
        agent = ACPAgent(
            acp_command=["echo"],
            agent_context=AgentContext(
                skills=[
                    Skill(
                        name="review",
                        content="Review instructions",
                        trigger=KeywordTrigger(keywords=["/review"]),
                    )
                ]
            ),
        )

        self._init_with_patches(agent, tmp_path)

    def test_allows_agent_context_with_secrets(self, tmp_path):
        """Secrets are now ACP-compatible: they are injected into the subprocess
        env by _start_acp_server and advertised in the prompt via <CUSTOM_SECRETS>."""
        agent = ACPAgent(
            acp_command=["echo"],
            agent_context=AgentContext(secrets={"GITHUB_TOKEN": "ghp_secret"}),
        )
        # Should not raise
        self._init_with_patches(agent, tmp_path)

    def test_agent_context_to_acp_prompt_context(self):
        context = AgentContext(
            skills=[
                Skill(
                    name="review",
                    content="Full review instructions",
                    trigger=KeywordTrigger(keywords=["/review"]),
                    description="Review pull requests.",
                )
            ],
            system_message_suffix="Follow repository rules.",
            user_message_suffix="Prefer concise responses.",
            current_datetime="2026-04-24T00:00:00",
        )

        prompt = context.to_acp_prompt_context()

        assert prompt is not None
        # Reuses the same system_message_suffix.j2 template as the general
        # agent, so the rendered sections are identical.
        assert "<CURRENT_DATETIME>" in prompt
        assert "2026-04-24T00:00:00" in prompt
        assert "<name>review</name>" in prompt
        assert "<description>Review pull requests.</description>" in prompt
        assert "Full review instructions" not in prompt
        assert "Follow repository rules." in prompt
        # user_message_suffix is not emitted by to_acp_prompt_context because
        # LocalConversation already applies it via event.to_llm_message().
        assert "Prefer concise responses." not in prompt

    def test_agent_context_to_acp_prompt_context_returns_none_when_empty(self):
        context = AgentContext(skills=[], current_datetime=None)

        assert context.to_acp_prompt_context() is None

    def test_agent_context_to_acp_prompt_context_emits_datetime_by_default(self):
        context = AgentContext(skills=[])

        prompt = context.to_acp_prompt_context()
        assert prompt is not None
        assert "<CURRENT_DATETIME>" in prompt

    def test_agent_context_to_acp_prompt_context_includes_secrets(self):
        """Secrets appear in the ACP prompt as a <CUSTOM_SECRETS> block so the
        ACP subprocess knows which environment variables are available."""
        from openhands.sdk.secret import StaticSecret
        from pydantic import SecretStr

        context = AgentContext(
            secrets={
                "GITHUB_TOKEN": StaticSecret(
                    value=SecretStr("ghp_secret"),
                    description="GitHub authentication token",
                ),
                "MY_API_KEY": StaticSecret(value=SecretStr("key123")),
            },
            current_datetime=None,
        )

        prompt = context.to_acp_prompt_context()

        assert prompt is not None
        assert "<CUSTOM_SECRETS>" in prompt
        assert "$GITHUB_TOKEN" in prompt
        assert "GitHub authentication token" in prompt
        assert "$MY_API_KEY" in prompt

    def test_agent_context_to_acp_prompt_context_includes_legacy_repo_skills(self):
        context = AgentContext(
            skills=[
                Skill(
                    name="claude",
                    content="Always follow the repository review checklist.",
                    trigger=None,
                ),
                Skill(
                    name="repo-skill",
                    content="Full AgentSkills instructions should stay out.",
                    description="Use repo-specific tools.",
                    is_agentskills_format=True,
                ),
            ],
            current_datetime=None,
        )

        prompt = context.to_acp_prompt_context()

        assert prompt is not None
        assert "<REPO_CONTEXT>" in prompt
        assert "[BEGIN context from [claude]]" in prompt
        assert "Always follow the repository review checklist." in prompt
        assert "<name>repo-skill</name>" in prompt
        assert "<description>Use repo-specific tools.</description>" in prompt
        assert "Full AgentSkills instructions should stay out." not in prompt
        assert "<name>claude</name>" not in prompt

    def test_agent_context_to_acp_prompt_context_lists_legacy_triggered_skills(self):
        context = AgentContext(
            skills=[
                Skill(
                    name="roasted-review",
                    content="Use a stricter review tone.",
                    trigger=KeywordTrigger(keywords=["/roasted"]),
                    description="Run a stricter review.",
                )
            ],
            current_datetime=None,
        )

        prompt = context.to_acp_prompt_context()

        assert prompt is not None
        assert "<REPO_CONTEXT>" not in prompt
        assert "<name>roasted-review</name>" in prompt
        assert "<description>Run a stricter review.</description>" in prompt
        assert "Use a stricter review tone." not in prompt

    def test_build_acp_prompt_preserves_all_text_blocks(self):
        agent = _make_agent(
            agent_context=AgentContext(
                user_message_suffix="Prefer concise responses.",
                current_datetime=None,
            )
        )
        event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[
                    TextContent(text="First block."),
                    TextContent(text="Second block."),
                ],
            ),
            extended_content=[TextContent(text="Prefer concise responses.")],
        )

        prompt = agent._build_acp_prompt(event)

        assert prompt is not None
        assert "First block." in prompt
        assert "Second block." in prompt
        assert prompt.count("Prefer concise responses.") == 1


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
# Activity heartbeat
# ---------------------------------------------------------------------------


class TestACPActivityHeartbeat:
    """Tests for the on_activity heartbeat in _OpenHandsACPBridge."""

    def test_reset_clears_on_activity(self):
        client = _OpenHandsACPBridge()
        client.on_activity = lambda: None
        client.reset()
        assert client.on_activity is None

    def test_reset_preserves_last_activity_signal(self):
        """_last_activity_signal persists across resets (like telemetry state)."""
        client = _OpenHandsACPBridge()
        client._last_activity_signal = 999.0
        client.reset()
        assert client._last_activity_signal == 999.0

    @pytest.mark.asyncio
    async def test_tool_call_start_signals_activity(self):
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()
        signals: list[bool] = []
        client.on_activity = lambda: signals.append(True)

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Read file"
        start.kind = "read"
        start.status = "in_progress"
        start.raw_input = None
        start.raw_output = None
        start.content = None

        await client.session_update("sess-1", start)
        assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_tool_call_progress_signals_activity(self):
        from acp.schema import ToolCallProgress, ToolCallStart

        client = _OpenHandsACPBridge()
        signals: list[bool] = []
        client.on_activity = lambda: signals.append(True)

        # Need a ToolCallStart first
        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Read"
        start.kind = "read"
        start.status = "in_progress"
        start.raw_input = None
        start.raw_output = None
        start.content = None
        await client.session_update("sess-1", start)

        # Reset throttle so ToolCallProgress can fire
        client._last_activity_signal = float("-inf")
        signals.clear()

        progress = MagicMock(spec=ToolCallProgress)
        progress.tool_call_id = "tc-1"
        progress.title = None
        progress.kind = None
        progress.status = "completed"
        progress.raw_input = None
        progress.raw_output = "ok"
        progress.content = None
        await client.session_update("sess-1", progress)
        assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_agent_message_chunk_signals_activity(self):
        from acp.schema import AgentMessageChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        signals: list[bool] = []
        client.on_activity = lambda: signals.append(True)

        chunk = MagicMock(spec=AgentMessageChunk)
        chunk.content = MagicMock(spec=TextContentBlock)
        chunk.content.text = "hello"

        await client.session_update("sess-1", chunk)
        assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_activity_signal_is_throttled(self):
        """Signals should be throttled to at most one per interval."""
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()
        signals: list[bool] = []
        client.on_activity = lambda: signals.append(True)

        for i in range(5):
            start = MagicMock(spec=ToolCallStart)
            start.tool_call_id = f"tc-{i}"
            start.title = f"Tool {i}"
            start.kind = "read"
            start.status = "completed"
            start.raw_input = None
            start.raw_output = None
            start.content = None
            await client.session_update("sess-1", start)

        # All happened within the same throttle window → only 1 signal
        assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_no_signal_without_callback(self):
        """No error when on_activity is None."""
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()
        assert client.on_activity is None

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Tool"
        start.kind = "read"
        start.status = "completed"
        start.raw_input = None
        start.raw_output = None
        start.content = None

        await client.session_update("sess-1", start)  # Should not raise

    @pytest.mark.asyncio
    async def test_activity_callback_error_is_swallowed(self):
        """Errors in on_activity must not break session_update."""
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()
        client.on_activity = MagicMock(side_effect=RuntimeError("boom"))

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Tool"
        start.kind = "read"
        start.status = "completed"
        start.raw_input = None
        start.raw_output = None
        start.content = None

        await client.session_update("sess-1", start)  # Should not raise
        client.on_activity.assert_called_once()

    def test_step_wires_on_activity(self, tmp_path):
        """step() should set on_activity on the bridge from _on_activity."""
        agent = _make_agent()
        state = _make_state(tmp_path)

        # Wire up a user message
        state.events.append(
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="sys"),
                tools=[],
            )
        )
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="test")]),
            ),
        )

        activity_fn = MagicMock()
        agent._on_activity = activity_fn

        # Mock the internals so step() doesn't actually call the ACP server
        agent._client = _OpenHandsACPBridge()

        # Capture on_activity while prompt() is still "running" — step()
        # unwires the bridge callbacks in its finally block once the turn
        # completes, so the post-return value is None by design.
        wired_during_prompt: list = []

        def _capture_run_async(_coro, **_kwargs):
            wired_during_prompt.append(agent._client.on_activity)
            return MagicMock(usage=None)

        agent._executor = MagicMock()
        agent._executor.run_async = _capture_run_async
        agent._session_id = "sess-1"
        agent._initialized = True

        conversation = MagicMock()
        conversation.state = state
        events: list = []

        agent.step(conversation, on_event=events.append)

        # Verify on_activity was wired to the bridge during the turn.
        assert wired_during_prompt == [activity_fn]
        # And that it was cleared afterward so a late session_update
        # cannot fire the per-turn heartbeat callback out-of-band.
        assert agent._client.on_activity is None


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

        # step() emits MessageEvent + ActionEvent(FinishAction)
        # + ObservationEvent(FinishObservation)
        assert len(events) == 3
        assert isinstance(events[0], MessageEvent)
        assert events[0].source == "agent"
        content_block = events[0].llm_message.content[0]
        assert isinstance(content_block, TextContent)
        assert content_block.text == "The answer is 4"

    @staticmethod
    def _wire_passthrough_mocks(agent: ACPAgent) -> None:
        """Wire mock ACP internals that relay prompt() calls through asyncio."""
        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._conn.prompt = AsyncMock(return_value=None)
        agent._session_id = "test-session"

        def _fake_run_async(coro_factory, **_kwargs):
            return asyncio.run(coro_factory())

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

    def test_step_sends_skill_catalog_to_acp_server(self, tmp_path):
        agent = _make_agent(
            agent_context=AgentContext(
                skills=[
                    Skill(
                        name="review",
                        content="Full review instructions that ACP should not receive.",
                        trigger=KeywordTrigger(keywords=["/review"]),
                        description="Review pull requests.",
                    )
                ]
            )
        )
        state = _make_state(tmp_path)
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(
                    role="user",
                    content=[TextContent(text="Review this PR.")],
                ),
                extended_content=[
                    TextContent(
                        text="<skill_context>Use strict review.</skill_context>"
                    )
                ],
            )
        )
        conversation = MagicMock()
        conversation.state = state
        self._wire_passthrough_mocks(agent)

        agent.step(conversation, on_event=lambda _: None)

        prompt_call = agent._conn.prompt.await_args
        assert prompt_call is not None
        prompt_blocks = prompt_call.args[0]
        assert len(prompt_blocks) == 1
        prompt_text = prompt_blocks[0].text
        assert "Review this PR." in prompt_text
        assert "<name>review</name>" in prompt_text
        assert "<description>Review pull requests.</description>" in prompt_text
        assert "<skill_context>Use strict review.</skill_context>" in prompt_text
        assert (
            "Full review instructions that ACP should not receive." not in prompt_text
        )

    def test_step_sends_legacy_repo_context_to_acp_server(self, tmp_path):
        agent = _make_agent(
            agent_context=AgentContext(
                skills=[
                    Skill(
                        name="claude",
                        content="Always follow repository-specific review rules.",
                        trigger=None,
                    ),
                    Skill(
                        name="agent-skill",
                        content="AgentSkills full instructions should not be sent.",
                        is_agentskills_format=True,
                        description="Use the agent skill catalog entry.",
                    ),
                ],
                current_datetime=None,
            )
        )
        state = _make_state(tmp_path)
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(
                    role="user",
                    content=[TextContent(text="Review this PR.")],
                ),
            )
        )
        conversation = MagicMock()
        conversation.state = state
        self._wire_passthrough_mocks(agent)

        agent.step(conversation, on_event=lambda _: None)

        prompt_call = agent._conn.prompt.await_args
        assert prompt_call is not None
        prompt_text = prompt_call.args[0][0].text
        assert "Review this PR." in prompt_text
        assert "<REPO_CONTEXT>" in prompt_text
        assert "Always follow repository-specific review rules." in prompt_text
        assert "<name>agent-skill</name>" in prompt_text
        assert (
            "<description>Use the agent skill catalog entry.</description>"
            in prompt_text
        )
        assert "AgentSkills full instructions should not be sent." not in prompt_text

    def test_step_sends_triggered_skill_content_to_acp_server(self, tmp_path):
        agent = _make_agent(
            agent_context=AgentContext(
                skills=[
                    Skill(
                        name="legacy-review",
                        content="Legacy triggered review instructions.",
                        trigger=KeywordTrigger(keywords=["/review"]),
                    ),
                    Skill(
                        name="agentskill-review",
                        content="AgentSkills triggered review instructions.",
                        trigger=KeywordTrigger(keywords=["/review"]),
                        is_agentskills_format=True,
                        description="AgentSkills review catalog.",
                    ),
                ],
                current_datetime=None,
            )
        )
        state = _make_state(tmp_path)
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(
                    role="user",
                    content=[TextContent(text="/review this PR.")],
                ),
                extended_content=[
                    TextContent(text="Legacy triggered review instructions."),
                    TextContent(text="AgentSkills triggered review instructions."),
                ],
            )
        )
        conversation = MagicMock()
        conversation.state = state
        self._wire_passthrough_mocks(agent)

        agent.step(conversation, on_event=lambda _: None)

        prompt_call = agent._conn.prompt.await_args
        assert prompt_call is not None
        prompt_text = prompt_call.args[0][0].text
        assert "Legacy triggered review instructions." in prompt_text
        assert "AgentSkills triggered review instructions." in prompt_text
        assert "<name>agentskill-review</name>" in prompt_text
        assert "<description>AgentSkills review catalog.</description>" in prompt_text

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

        with pytest.raises(RuntimeError, match="boom"):
            agent.step(conversation, on_event=events.append)

        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
        assert len(events) >= 1
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

        # Capture on_token while prompt() is still running — step() clears
        # the per-turn callbacks in its finally block once the turn ends.
        wired_during_prompt: list = []

        def _fake_run_async(_coro, **_kwargs):
            wired_during_prompt.append(mock_client.on_token)
            mock_client.accumulated_text.append("ok")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        on_token = MagicMock()

        agent.step(conversation, on_event=lambda _: None, on_token=on_token)

        # Verify on_token was wired during the turn.
        assert wired_during_prompt == [on_token]
        # And unwired afterward so a late token chunk is a no-op.
        assert mock_client.on_token is None


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

    def _make_step_fixtures(self, tmp_path, agent=None, usage=None, cost=None):
        """Set up agent + client + executor for step() telemetry tests."""
        if agent is None:
            agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = agent._client or _OpenHandsACPBridge()
        mock_client._context_window = 200000
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        mock_response = MagicMock()
        if usage is not None:
            mock_usage = MagicMock()
            mock_usage.input_tokens = usage.get("input", 0)
            mock_usage.output_tokens = usage.get("output", 0)
            mock_usage.cached_read_tokens = usage.get("cache_read", 0)
            mock_usage.cached_write_tokens = usage.get("cache_write", 0)
            mock_usage.thought_tokens = usage.get("thought", 0)
            mock_response.usage = mock_usage
        else:
            mock_response.usage = None
            mock_response.field_meta = None

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("response text")
            if cost is not None:
                mock_update = MagicMock()
                mock_update.cost = MagicMock()
                mock_update.cost.amount = cost[0]
                mock_update.size = cost[1]
                mock_client._turn_usage_updates["test-session"] = mock_update
                mock_client._context_window_by_session["test-session"] = cost[1]
                mock_client._context_window = cost[1]
            return mock_response

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        return agent, conversation

    def test_step_records_token_usage(self, tmp_path):
        """step() records per-turn token usage from PromptResponse.usage."""
        agent, conversation = self._make_step_fixtures(
            tmp_path,
            usage={
                "input": 100,
                "output": 50,
                "cache_read": 10,
                "cache_write": 5,
                "thought": 20,
            },
            cost=(0.05, 200000),
        )

        agent.step(conversation, on_event=lambda _: None)

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
        agent, conversation = self._make_step_fixtures(tmp_path)

        agent.step(conversation, on_event=lambda _: None)

        assert len(agent.llm.metrics.token_usages) == 0

    def test_step_records_cost_from_usage_update(self, tmp_path):
        """step() records cost from UsageUpdate in the single telemetry path."""
        agent, conversation = self._make_step_fixtures(
            tmp_path,
            usage={"input": 100, "output": 50},
            cost=(0.05, 128000),
        )

        agent.step(conversation, on_event=lambda _: None)

        assert agent.llm.metrics.accumulated_cost == pytest.approx(0.05)
        assert len(agent.llm.metrics.costs) == 1
        assert agent._client._last_cost == pytest.approx(0.05)

    def test_step_records_incremental_cost(self, tmp_path):
        """Cost tracking is incremental across turns."""
        agent = _make_agent()

        _, conversation1 = self._make_step_fixtures(
            tmp_path,
            agent=agent,
            usage={"input": 100, "output": 50},
            cost=(0.05, 128000),
        )
        agent.step(conversation1, on_event=lambda _: None)
        assert agent.llm.metrics.accumulated_cost == pytest.approx(0.05)

        _, conversation2 = self._make_step_fixtures(
            tmp_path,
            agent=agent,
            usage={"input": 200, "output": 100},
            cost=(0.12, 130000),
        )
        agent.step(conversation2, on_event=lambda _: None)
        assert agent.llm.metrics.accumulated_cost == pytest.approx(0.12)
        assert len(agent.llm.metrics.costs) == 2

    def test_step_no_cost_when_usage_update_missing(self, tmp_path):
        """No cost is recorded when PromptResponse arrives without UsageUpdate."""
        agent, conversation = self._make_step_fixtures(
            tmp_path,
            usage={"input": 100, "output": 50},
            cost=None,
        )

        agent.step(conversation, on_event=lambda _: None)

        assert agent.llm.metrics.accumulated_cost == 0.0
        assert len(agent.llm.metrics.costs) == 0
        assert len(agent.llm.metrics.token_usages) == 1

    def test_step_records_partial_metrics_on_usage_timeout(self, tmp_path, caplog):
        """Timeout waiting for UsageUpdate logs warning but records token metrics."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.cached_read_tokens = 0
        mock_usage.cached_write_tokens = 0
        mock_usage.thought_tokens = 0

        mock_response = MagicMock()
        mock_response.usage = mock_usage

        async def _fake_prompt(*_args, **_kwargs):
            return mock_response

        def _run_async(coro_fn, **_kwargs):
            loop = asyncio.new_event_loop()
            try:
                agent._conn.prompt = _fake_prompt
                return loop.run_until_complete(coro_fn())
            finally:
                loop.close()

        mock_executor = MagicMock()
        mock_executor.run_async = _run_async
        agent._executor = mock_executor

        async def _raise_timeout(awaitable, timeout):
            awaitable.close()
            raise TimeoutError

        with patch(
            "openhands.sdk.agent.acp_agent.asyncio.wait_for",
            new=AsyncMock(side_effect=_raise_timeout),
        ):
            agent.step(conversation, on_event=lambda _: None)

        assert "UsageUpdate not received within 2.0s" in caplog.text
        assert len(agent.llm.metrics.token_usages) == 1
        assert len(agent.llm.metrics.costs) == 0
        assert agent.llm.metrics.accumulated_cost == 0.0

    def test_step_records_latency(self, tmp_path):
        """step() records response latency in the single telemetry path."""
        agent, conversation = self._make_step_fixtures(tmp_path)

        agent.step(conversation, on_event=lambda _: None)

        assert len(agent.llm.metrics.response_latencies) == 1
        assert agent.llm.metrics.response_latencies[0].latency >= 0.0

    @pytest.mark.asyncio
    async def test_session_update_stores_usage_update(self):
        """session_update() stores UsageUpdate for step() to process later."""
        from acp.schema import UsageUpdate

        client = _OpenHandsACPBridge()
        usage_event = client.prepare_usage_sync("sess-1")

        update = MagicMock(spec=UsageUpdate)
        update.size = 128000
        update.cost = MagicMock()
        update.cost.amount = 0.05

        await client.session_update("sess-1", update)

        assert client.get_turn_usage_update("sess-1") is update
        assert client._context_window == 128000
        assert client._context_window_by_session["sess-1"] == 128000
        assert usage_event.is_set()

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
        assert client._context_window_by_session["sess-1"] == 200000

    def test_stats_callback_invoked(self, tmp_path):
        """After step(), the sentinel LLM's stats callback is invoked."""
        agent, conversation = self._make_step_fixtures(tmp_path)

        callback = MagicMock()
        agent.llm.telemetry._stats_update_callback = callback

        agent.step(conversation, on_event=lambda _: None)

        callback.assert_called_once()

    def test_init_state_sets_bridge_client(self, tmp_path):
        """init_state() keeps the bridge instance installed by _start_acp_server."""
        agent = _make_agent()
        state = _make_state(tmp_path)
        expected_client = _OpenHandsACPBridge()

        with patch(
            "openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"
        ) as mock_start:

            def fake_start(_state):
                agent._client = expected_client

            mock_start.side_effect = fake_start
            agent.init_state(state, on_event=lambda _: None)

        assert agent._client is expected_client

    def test_reset_preserves_telemetry_state(self):
        """reset() clears per-turn buffers but preserves cumulative telemetry."""
        client = _OpenHandsACPBridge()
        client._last_cost = 1.23
        client._last_cost_by_session["sess-1"] = 1.23
        client._context_window = 128000
        client._context_window_by_session["sess-1"] = 128000
        client._turn_usage_updates["sess-1"] = MagicMock()
        client._usage_received["sess-1"] = asyncio.Event()
        client.accumulated_text.append("hello")
        client.accumulated_thoughts.append("thinking")

        client.reset()

        assert client.accumulated_text == []
        assert client.accumulated_thoughts == []
        assert client._last_cost == 1.23
        assert client._context_window == 128000
        assert client._last_cost_by_session["sess-1"] == 1.23
        assert client._context_window_by_session["sess-1"] == 128000
        assert client._turn_usage_updates == {}
        assert client._usage_received == {}


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
        start.content = None

        await client.session_update("sess-1", start)

        assert len(client.accumulated_tool_calls) == 1
        tc = client.accumulated_tool_calls[0]
        assert tc["tool_call_id"] == "tc-1"
        assert tc["title"] == "Read file"
        assert tc["tool_kind"] == "read"
        assert tc["status"] == "in_progress"
        assert tc["raw_input"] == {"path": "/tmp/test.py"}
        assert tc["raw_output"] is None
        assert tc["content"] is None

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
        start.content = None

        await client.session_update("sess-1", start)

        # Progress
        progress = MagicMock(spec=ToolCallProgress)
        progress.tool_call_id = "tc-2"
        progress.title = None  # not updated
        progress.kind = None  # not updated
        progress.status = "completed"
        progress.raw_input = None  # not updated
        progress.raw_output = "file1.py\nfile2.py"
        progress.content = None

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
            start.content = None
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


class TestACPToolCallLiveEmission:
    """Tests that ``session_update`` fires ``on_event`` live (not batched).

    Closes OpenHands/software-agent-sdk#2866: tool-call events must reach
    ``on_event`` as each ACP notification arrives, so the event stream
    reflects real subprocess progress instead of a single end-of-turn burst.
    """

    @pytest.mark.asyncio
    async def test_session_update_fires_on_event_live(self):
        """Each ToolCallStart/Progress triggers an immediate on_event call."""
        from acp.schema import ToolCallProgress, ToolCallStart

        client = _OpenHandsACPBridge()
        events: list = []
        client.on_event = events.append

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Read file"
        start.kind = "read"
        start.status = "in_progress"
        start.raw_input = {"path": "/a"}
        start.raw_output = None
        start.content = None
        await client.session_update("sess", start)

        # on_event fires synchronously — event already present, not batched.
        assert len(events) == 1
        assert isinstance(events[0], ACPToolCallEvent)
        assert events[0].tool_call_id == "tc-1"
        assert events[0].status == "in_progress"
        assert events[0].raw_output is None

        progress = MagicMock(spec=ToolCallProgress)
        progress.tool_call_id = "tc-1"
        progress.title = None
        progress.kind = None
        progress.status = "completed"
        progress.raw_input = None
        progress.raw_output = "hello"
        progress.content = None
        await client.session_update("sess", progress)

        # Same tool_call_id, evolving status/raw_output — consumer dedupes.
        assert len(events) == 2
        assert events[1].tool_call_id == "tc-1"
        assert events[1].status == "completed"
        assert events[1].raw_output == "hello"
        assert events[1].is_error is False

    @pytest.mark.asyncio
    async def test_session_update_preserves_interleaved_order(self):
        """Tool-call and text-chunk updates reach callbacks in arrival order.

        The bridge emits on_event synchronously from session_update, so the
        order consumers see is exactly the order the ACP subprocess sent them.
        Text/thought chunks are routed to on_token rather than on_event, but
        the *combined* callback stream must stay in arrival order so that
        consumers can rebuild a coherent trace.
        """
        from acp.schema import (
            AgentMessageChunk,
            AgentThoughtChunk,
            TextContentBlock,
            ToolCallProgress,
            ToolCallStart,
        )

        client = _OpenHandsACPBridge()
        # Single timeline of callback arrivals, tagged by source.
        observed: list[tuple[str, Any]] = []
        client.on_event = lambda e: observed.append(("event", e))
        client.on_token = lambda t: observed.append(("token", t))

        def make_start(tc_id: str) -> Any:
            s = MagicMock(spec=ToolCallStart)
            s.tool_call_id = tc_id
            s.title = f"Tool {tc_id}"
            s.kind = "read"
            s.status = "in_progress"
            s.raw_input = None
            s.raw_output = None
            s.content = None
            return s

        def make_progress(tc_id: str, status: str) -> Any:
            p = MagicMock(spec=ToolCallProgress)
            p.tool_call_id = tc_id
            p.title = None
            p.kind = None
            p.status = status
            p.raw_input = None
            p.raw_output = None
            p.content = None
            return p

        def make_text_chunk(text: str) -> Any:
            c = MagicMock(spec=AgentMessageChunk)
            c.content = MagicMock(spec=TextContentBlock)
            c.content.text = text
            return c

        def make_thought_chunk(text: str) -> Any:
            c = MagicMock(spec=AgentThoughtChunk)
            c.content = MagicMock(spec=TextContentBlock)
            c.content.text = text
            return c

        sequence: list = [
            make_thought_chunk("thinking..."),
            make_start("tc-a"),
            make_text_chunk("reading "),
            make_progress("tc-a", "completed"),
            make_start("tc-b"),
            make_text_chunk("done"),
            make_progress("tc-b", "completed"),
        ]
        for update in sequence:
            await client.session_update("sess", update)

        # Thought chunks don't fire a callback today — filter to the callback
        # kinds we drove and confirm arrival order matches the driven sequence.
        expected_stream = [
            "event",  # tc-a start
            "token",  # text chunk
            "event",  # tc-a progress
            "event",  # tc-b start
            "token",  # text chunk
            "event",  # tc-b progress
        ]
        assert [kind for kind, _ in observed] == expected_stream
        tool_events = [payload for kind, payload in observed if kind == "event"]
        assert [e.tool_call_id for e in tool_events] == [
            "tc-a",
            "tc-a",
            "tc-b",
            "tc-b",
        ]
        assert [e.status for e in tool_events] == [
            "in_progress",
            "completed",
            "in_progress",
            "completed",
        ]

    @pytest.mark.asyncio
    async def test_session_update_no_on_event_when_unset(self):
        """When on_event is None (no active step), session_update is a no-op emit."""
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()
        assert client.on_event is None

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Read"
        start.kind = "read"
        start.status = "in_progress"
        start.raw_input = None
        start.raw_output = None
        start.content = None

        # Must not raise
        await client.session_update("sess", start)
        # Still accumulated so step() can reference it if needed.
        assert len(client.accumulated_tool_calls) == 1

    @pytest.mark.asyncio
    async def test_on_event_errors_are_swallowed(self):
        """A raising on_event must not break the session_update pipeline."""
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()
        client.on_event = MagicMock(side_effect=RuntimeError("boom"))

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Read"
        start.kind = "read"
        start.status = "in_progress"
        start.raw_input = None
        start.raw_output = None
        start.content = None

        await client.session_update("sess", start)  # must not raise
        client.on_event.assert_called_once()

    def test_reset_clears_on_event(self):
        """reset() clears on_event so the next step wires a fresh callback."""
        client = _OpenHandsACPBridge()
        client.on_event = lambda _: None
        client.reset()
        assert client.on_event is None


class TestACPCancelInflightToolCalls:
    """Tests for _cancel_inflight_tool_calls — ensures ghost tool cards are
    closed on retry / abort so the live-emission stream cannot leave an
    orphaned pending event on ``state.events``.

    Raised in PR review on #2866: ACP servers mint fresh ``tool_call_id``s
    when the prompt is retried, so any pending event already fired for the
    failed attempt would otherwise spin forever under dedup-by-id consumers.
    """

    @staticmethod
    def _push_entry(
        client: _OpenHandsACPBridge, tool_call_id: str, status: str
    ) -> None:
        client.accumulated_tool_calls.append(
            {
                "tool_call_id": tool_call_id,
                "title": f"Tool {tool_call_id}",
                "tool_kind": "read",
                "status": status,
                "raw_input": {"k": "v"},
                "raw_output": None,
                "content": None,
            }
        )

    def test_emits_failed_event_for_pending_entries(self, tmp_path):
        """Pending / in_progress entries get a terminal failed ACPToolCallEvent."""
        agent = _make_agent()
        agent._client = _OpenHandsACPBridge()
        emitted: list = []
        agent._client.on_event = emitted.append
        self._push_entry(agent._client, "tc-1", "pending")
        self._push_entry(agent._client, "tc-2", "in_progress")

        agent._cancel_inflight_tool_calls()

        assert len(emitted) == 2
        assert all(isinstance(e, ACPToolCallEvent) for e in emitted)
        assert [e.tool_call_id for e in emitted] == ["tc-1", "tc-2"]
        assert all(e.status == "failed" and e.is_error for e in emitted)

    def test_skips_already_terminal_entries(self, tmp_path):
        """completed / failed entries are left alone — they already closed."""
        agent = _make_agent()
        agent._client = _OpenHandsACPBridge()
        emitted: list = []
        agent._client.on_event = emitted.append
        self._push_entry(agent._client, "tc-done", "completed")
        self._push_entry(agent._client, "tc-bad", "failed")
        self._push_entry(agent._client, "tc-live", "pending")

        agent._cancel_inflight_tool_calls()

        # Only the pending one gets a synthetic terminal event.
        assert [e.tool_call_id for e in emitted] == ["tc-live"]

    def test_callback_errors_are_swallowed(self):
        """A raising on_event during cancellation must not break the retry path."""
        agent = _make_agent()
        agent._client = _OpenHandsACPBridge()
        self._push_entry(agent._client, "tc-1", "pending")
        self._push_entry(agent._client, "tc-2", "pending")

        seen: list = []

        def flaky(event) -> None:
            seen.append(event)
            raise RuntimeError("boom")

        agent._client.on_event = flaky
        agent._cancel_inflight_tool_calls()  # must not raise
        # Both entries still attempted even though the first raised.
        assert len(seen) == 2

    def test_noop_when_on_event_unset(self):
        """If no on_event is wired, cancellation quietly does nothing."""
        agent = _make_agent()
        agent._client = _OpenHandsACPBridge()
        self._push_entry(agent._client, "tc-1", "pending")

        # on_event default is None — must not raise, must not iterate
        assert agent._client.on_event is None
        agent._cancel_inflight_tool_calls()

    def test_retry_cancels_pending_events_before_reset(self, tmp_path):
        """Full step() retry path closes pending cards before the new attempt."""
        from acp.schema import ToolCallStart

        agent = _make_agent()
        state = _make_state(tmp_path)
        state.events.append(
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="sys"),
                tools=[],
            )
        )
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="go")]),
            )
        )
        conversation = MagicMock()
        conversation.state = state

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        events: list = []
        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: stream a pending tool call, then fail
                start = MagicMock(spec=ToolCallStart)
                start.tool_call_id = "toolu_AAA"
                start.title = "Read file"
                start.kind = "read"
                start.status = "pending"
                start.raw_input = {"path": "/tmp/x"}
                start.raw_output = None
                start.content = None
                asyncio.run(mock_client.session_update("sess", start))
                raise ConnectionError("reset by peer")
            # Retry: fresh tool call id reaches terminal state
            start = MagicMock(spec=ToolCallStart)
            start.tool_call_id = "toolu_BBB"
            start.title = "Read file"
            start.kind = "read"
            start.status = "completed"
            start.raw_input = {"path": "/tmp/x"}
            start.raw_output = "ok"
            start.content = None
            asyncio.run(mock_client.session_update("sess", start))
            mock_client.accumulated_text.append("done")
            return MagicMock(usage=None)

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with patch("openhands.sdk.agent.acp_agent.time.sleep"):
            agent.step(conversation, on_event=events.append)

        assert call_count == 2
        tool_events = [e for e in events if isinstance(e, ACPToolCallEvent)]
        # Expected sequence:
        #   toolu_AAA(pending)  — live-emitted during attempt 1
        #   toolu_AAA(failed)   — synthetic cancellation before retry reset
        #   toolu_BBB(completed) — attempt 2
        by_id: dict[str, list[ACPToolCallEvent]] = {}
        for e in tool_events:
            by_id.setdefault(e.tool_call_id, []).append(e)

        assert "toolu_AAA" in by_id
        aaa_events = by_id["toolu_AAA"]
        # Must end in a terminal status so consumer dedupe-by-id closes the card.
        assert aaa_events[-1].status == "failed"
        assert aaa_events[-1].is_error is True

        assert "toolu_BBB" in by_id
        assert by_id["toolu_BBB"][-1].status == "completed"

        # The toolu_AAA cancellation comes before any toolu_BBB event.
        aaa_idx = max(
            i for i, e in enumerate(tool_events) if e.tool_call_id == "toolu_AAA"
        )
        bbb_idx = min(
            i for i, e in enumerate(tool_events) if e.tool_call_id == "toolu_BBB"
        )
        assert aaa_idx < bbb_idx


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
        """Tool-call events reach on_event live, ahead of the MessageEvent."""
        from acp.schema import ToolCallStart

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            # Simulate the ACP subprocess streaming two tool-call notifications
            # during prompt(). session_update fires on_event synchronously,
            # so these events appear before run_async returns.
            for tool_call_id, title, kind, status, raw_input, raw_output in [
                (
                    "tc-1",
                    "Read file",
                    "read",
                    "completed",
                    {"path": "/tmp/f.py"},
                    "content",
                ),
                ("tc-2", "Execute bash", "execute", "failed", {"command": "ls"}, None),
            ]:
                start = MagicMock(spec=ToolCallStart)
                start.tool_call_id = tool_call_id
                start.title = title
                start.kind = kind
                start.status = status
                start.raw_input = raw_input
                start.raw_output = raw_output
                start.content = None
                asyncio.run(mock_client.session_update("sess", start))
            mock_client.accumulated_text.append("done")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        # Should be: 2 tool call events (live) + 1 message event
        # + finish action + finish observation
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

    def test_step_clears_live_callbacks_on_return(self, tmp_path):
        """After step() returns, bridge callbacks are unwired.

        A trailing ``session_update`` that lands between turns (the ACP
        subprocess sending a late ``ToolCallProgress`` after its prompt
        response) would otherwise fire the previous step's ``on_event``
        on the portal thread with no FIFOLock held by anyone, racing
        other threads appending to ``state.events``.
        """
        from acp.schema import ToolCallStart

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            mock_client.accumulated_text.append("done")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append, on_token=lambda _: None)

        # Callbacks unwired — a late session_update is a safe no-op emit.
        assert mock_client.on_event is None
        assert mock_client.on_token is None
        assert mock_client.on_activity is None

        pre_count = len(events)
        trailing = MagicMock(spec=ToolCallStart)
        trailing.tool_call_id = "tc-late"
        trailing.title = "Late arrival"
        trailing.kind = "read"
        trailing.status = "completed"
        trailing.raw_input = None
        trailing.raw_output = None
        trailing.content = None
        asyncio.run(mock_client.session_update("sess", trailing))
        assert len(events) == pre_count  # nothing reached the stale callback

    def test_step_clears_live_callbacks_on_error(self, tmp_path):
        """Callback unwire also runs when step() raises (finally block)."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            raise RuntimeError("boom")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with pytest.raises(RuntimeError):
            agent.step(conversation, on_event=events.append)

        assert mock_client.on_event is None
        assert mock_client.on_token is None
        assert mock_client.on_activity is None

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


# ---------------------------------------------------------------------------
# _resolve_bypass_mode
# ---------------------------------------------------------------------------


class TestResolveBypassMode:
    def test_claude_agent(self):
        assert _resolve_bypass_mode("claude-agent-acp") == "bypassPermissions"

    def test_claude_agent_with_scope(self):
        assert (
            _resolve_bypass_mode("@agentclientprotocol/claude-agent-acp")
            == "bypassPermissions"
        )

    def test_codex_acp(self):
        assert _resolve_bypass_mode("codex-acp") == "full-access"

    def test_codex_acp_with_version(self):
        assert _resolve_bypass_mode("Codex-ACP v0.9.2") == "full-access"

    def test_unknown_server_defaults_to_full_access(self):
        assert _resolve_bypass_mode("some-other-agent") == "full-access"

    def test_empty_name_defaults_to_full_access(self):
        assert _resolve_bypass_mode("") == "full-access"


# ---------------------------------------------------------------------------
# acp_session_mode field
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _select_auth_method
# ---------------------------------------------------------------------------


class TestSelectAuthMethod:
    """Test auto-detection of ACP auth method from env vars."""

    @staticmethod
    def _make_auth_method(method_id: str) -> MagicMock:
        m = MagicMock()
        m.id = method_id
        return m

    def test_openai_api_key(self):
        methods = [
            self._make_auth_method("codex-api-key"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"OPENAI_API_KEY": "sk-test"}
        assert _select_auth_method(methods, env) == "openai-api-key"

    def test_codex_api_key_preferred_over_openai(self):
        """CODEX_API_KEY is checked first (appears first in the map)."""
        methods = [
            self._make_auth_method("codex-api-key"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"CODEX_API_KEY": "key1", "OPENAI_API_KEY": "key2"}
        assert _select_auth_method(methods, env) == "codex-api-key"

    def test_chatgpt_preferred_over_api_key(self, tmp_path):
        """ChatGPT subscription login takes precedence over API keys."""
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        auth_dir = tmp_path / ".codex"
        auth_dir.mkdir()
        (auth_dir / "auth.json").write_text("{}", encoding="utf-8")

        env = {"OPENAI_API_KEY": "sk-test"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "chatgpt"

    def test_api_key_fallback_when_no_chatgpt_file(self):
        """Falls back to API key when chatgpt is offered but auth file absent."""
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"OPENAI_API_KEY": "sk-test"}
        # Path.home() points to a real dir without .codex/auth.json
        assert _select_auth_method(methods, env) == "openai-api-key"

    def test_no_matching_credentials(self):
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"UNRELATED": "value"}
        assert _select_auth_method(methods, env) is None

    def test_chatgpt_auth_file(self, tmp_path):
        methods = [self._make_auth_method("chatgpt")]
        auth_dir = tmp_path / ".codex"
        auth_dir.mkdir()
        (auth_dir / "auth.json").write_text("{}", encoding="utf-8")

        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, {}) == "chatgpt"

    def test_empty_auth_methods(self):
        assert _select_auth_method([], {}) is None

    def test_method_not_in_server_list(self):
        """Even if env var is set, method must be offered by server."""
        methods = [self._make_auth_method("chatgpt")]
        env = {"OPENAI_API_KEY": "sk-test"}
        assert _select_auth_method(methods, env) is None


# ---------------------------------------------------------------------------
# ACP model overrides
# ---------------------------------------------------------------------------


class TestBuildSessionMeta:
    def test_claude_agent_adds_model_override(self):
        assert _build_session_meta("claude-agent-acp", "claude-opus-4-6") == {
            "claudeCode": {"options": {"model": "claude-opus-4-6"}}
        }

    def test_codex_agent_does_not_use_session_meta(self):
        assert _build_session_meta("codex-acp", "gpt-5.4") == {}

    def test_missing_model_does_not_add_session_meta(self):
        assert _build_session_meta("claude-agent-acp", None) == {}


class TestMaybeSetSessionModel:
    @pytest.mark.asyncio
    async def test_codex_agent_uses_protocol_model_override(self):
        conn = AsyncMock()
        await _maybe_set_session_model(conn, "codex-acp", "session-1", "gpt-5.4")
        conn.set_session_model.assert_awaited_once_with(
            model_id="gpt-5.4",
            session_id="session-1",
        )

    @pytest.mark.asyncio
    async def test_non_codex_agent_skips_protocol_override(self):
        conn = AsyncMock()
        await _maybe_set_session_model(
            conn,
            "claude-agent-acp",
            "session-1",
            "claude-opus-4-6",
        )
        conn.set_session_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_model_skips_protocol_override(self):
        conn = AsyncMock()
        await _maybe_set_session_model(conn, "codex-acp", "session-1", None)
        conn.set_session_model.assert_not_called()


# ---------------------------------------------------------------------------
# acp_session_mode field
# ---------------------------------------------------------------------------


class TestACPSessionMode:
    def test_default_is_none(self):
        agent = _make_agent()
        assert agent.acp_session_mode is None

    def test_can_set_explicit_mode(self):
        agent = ACPAgent(acp_command=["echo"], acp_session_mode="custom-mode")
        assert agent.acp_session_mode == "custom-mode"

    def test_serialization_roundtrip(self):
        agent = ACPAgent(
            acp_command=["codex-acp"],
            acp_session_mode="full-access",
        )
        dumped = agent.model_dump_json()
        restored = AgentBase.model_validate_json(dumped)
        assert isinstance(restored, ACPAgent)
        assert restored.acp_session_mode == "full-access"


# ---------------------------------------------------------------------------
# Connection retry logic
# ---------------------------------------------------------------------------


class TestACPPromptRetry:
    """Test retry logic for ACP prompt failures."""

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

    def test_retry_on_connection_error_then_success(self, tmp_path):
        """Retry succeeds after transient connection error."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection reset by peer")
            mock_client.accumulated_text.append("Success after retry")
            return MagicMock(usage=None)

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with patch("openhands.sdk.agent.acp_agent.time.sleep"):
            agent.step(conversation, on_event=events.append)

        assert call_count == 2
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        assert len(events) == 3
        assert "Success after retry" in events[0].llm_message.content[0].text

    def test_no_retry_on_non_connection_error(self, tmp_path):
        """Non-connection errors fail immediately without retry."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Some application error")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with pytest.raises(RuntimeError, match="Some application error"):
            agent.step(conversation, on_event=events.append)

        assert call_count == 1
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

    def test_no_retry_on_timeout(self, tmp_path):
        """Timeout errors are not retried."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("ACP prompt timed out")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=lambda _: None)

        assert call_count == 1
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

    def test_max_retries_exceeded(self, tmp_path):
        """Error raised after max retries exhausted."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Persistent connection failure")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with patch("openhands.sdk.agent.acp_agent.time.sleep"):
            with pytest.raises(ConnectionError, match="Persistent connection failure"):
                agent.step(conversation, on_event=events.append)

        assert call_count == 4
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

    def test_retry_on_acp_server_error_then_success(self, tmp_path):
        """Retry succeeds after transient ACP server error (JSON-RPC -32603)."""
        from acp.exceptions import RequestError as ACPRequestError

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ACPRequestError(-32603, "Internal Server Error")
            mock_client.accumulated_text.append("Success after server error retry")
            return MagicMock(usage=None)

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with patch("openhands.sdk.agent.acp_agent.time.sleep"):
            agent.step(conversation, on_event=events.append)

        assert call_count == 2
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        assert (
            "Success after server error retry" in events[0].llm_message.content[0].text
        )

    def test_no_retry_on_non_retriable_acp_error(self, tmp_path):
        """Non-retriable ACP error codes fail immediately."""
        from acp.exceptions import RequestError as ACPRequestError

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise ACPRequestError(-32600, "Invalid request")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with pytest.raises(ACPRequestError, match="Invalid request"):
            agent.step(conversation, on_event=events.append)

        assert call_count == 1  # No retry for non-retriable error codes
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

    def test_max_retries_exceeded_acp_server_error(self, tmp_path):
        """ACP server error raised after max retries exhausted."""
        from acp.exceptions import RequestError as ACPRequestError

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        call_count = 0

        def _fake_run_async(_coro, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise ACPRequestError(-32603, "Internal Server Error")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        with patch("openhands.sdk.agent.acp_agent.time.sleep"):
            with pytest.raises(ACPRequestError, match="Internal Server Error"):
                agent.step(conversation, on_event=events.append)

        # Default max retries is 3, so 4 total attempts
        assert call_count == 4
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR


# ---------------------------------------------------------------------------
# Gemini-specific tests
# ---------------------------------------------------------------------------


class TestGeminiBypassMode:
    def test_gemini_cli_uses_yolo(self):
        assert _resolve_bypass_mode("gemini-cli") == "yolo"

    def test_gemini_cli_with_version(self):
        assert _resolve_bypass_mode("gemini-cli/0.35.3") == "yolo"


class TestGeminiSessionModel:
    @pytest.mark.asyncio
    async def test_gemini_cli_uses_protocol_model_override(self):
        conn = AsyncMock()
        await _maybe_set_session_model(
            conn, "gemini-cli", "session-1", "gemini-3-flash"
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="gemini-3-flash",
            session_id="session-1",
        )


# ---------------------------------------------------------------------------
# _extract_token_usage
# ---------------------------------------------------------------------------


class TestExtractTokenUsage:
    def test_from_response_usage(self):
        """claude-agent-acp, codex-acp: standard response.usage field."""
        response = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 50
        response.usage.cached_read_tokens = 10
        response.usage.cached_write_tokens = 5
        response.usage.thought_tokens = 20
        assert _extract_token_usage(response) == (100, 50, 10, 5, 20)

    def test_from_field_meta_quota(self):
        """gemini-cli: _meta.quota.token_count fallback."""
        response = MagicMock()
        response.usage = None
        response.field_meta = {
            "quota": {"token_count": {"input_tokens": 200, "output_tokens": 80}}
        }
        assert _extract_token_usage(response) == (200, 80, 0, 0, 0)

    def test_none_response(self):
        assert _extract_token_usage(None) == (0, 0, 0, 0, 0)

    def test_no_usage_no_meta(self):
        response = MagicMock()
        response.usage = None
        response.field_meta = None
        assert _extract_token_usage(response) == (0, 0, 0, 0, 0)

    def test_empty_quota(self):
        response = MagicMock()
        response.usage = None
        response.field_meta = {"quota": {}}
        assert _extract_token_usage(response) == (0, 0, 0, 0, 0)


# ---------------------------------------------------------------------------
# _estimate_cost_from_tokens
# ---------------------------------------------------------------------------


class TestEstimateCostFromTokens:
    def test_unknown_model_returns_zero(self):
        assert _estimate_cost_from_tokens("nonexistent-model-xyz", 100, 50) == 0.0

    def test_zero_tokens_returns_zero(self):
        assert _estimate_cost_from_tokens("gemini-3-flash-preview", 0, 0) == 0.0

    def test_known_model_returns_positive(self):
        mock_cost_map = {
            "gemini-3-flash-preview": {
                "input_cost_per_token": 5e-07,
                "output_cost_per_token": 3e-06,
            }
        }
        mock_litellm = MagicMock()
        mock_litellm.model_cost = mock_cost_map
        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            cost = _estimate_cost_from_tokens("gemini-3-flash-preview", 1000, 500)
            assert cost == pytest.approx(1000 * 5e-07 + 500 * 3e-06)

    def test_import_failure_returns_zero(self):
        with patch.dict("sys.modules", {"litellm": None}):
            assert (
                _estimate_cost_from_tokens("gemini-3-flash-preview", 1000, 500) == 0.0
            )


# ---------------------------------------------------------------------------
# _serialize_tool_content
# ---------------------------------------------------------------------------


class TestSerializeToolContent:
    def test_none_returns_none(self):
        assert _serialize_tool_content(None) is None

    def test_empty_list_returns_none(self):
        assert _serialize_tool_content([]) is None

    def test_pydantic_model(self):
        model = MagicMock()
        model.model_dump.return_value = {
            "type": "diff",
            "path": "a.py",
            "old_text": "x",
            "new_text": "y",
        }
        result = _serialize_tool_content([model])
        assert result == [
            {"type": "diff", "path": "a.py", "old_text": "x", "new_text": "y"}
        ]
        model.model_dump.assert_called_once_with(mode="json")

    def test_plain_dict_passthrough(self):
        d = {"type": "content", "text": "hello"}
        result = _serialize_tool_content([d])
        assert result == [d]

    def test_mixed_content(self):
        model = MagicMock()
        model.model_dump.return_value = {"type": "diff", "path": "b.py"}
        d = {"type": "content", "text": "world"}
        result = _serialize_tool_content([model, d])
        assert result == [{"type": "diff", "path": "b.py"}, d]


# ---------------------------------------------------------------------------
# ACP session resume via ConversationState.agent_state (issue #2867)
# ---------------------------------------------------------------------------


class TestACPSessionIdPersistence:
    """Verify that the ACP session id is stashed in ``state.agent_state`` on
    first launch and that _start_acp_server reads it back on resume to drive
    load_session vs. new_session.
    """

    @staticmethod
    def _transport_patches(conn):
        """Context manager stacking the transport-layer mocks that let
        _start_acp_server run without spawning a real subprocess.
        """
        from contextlib import ExitStack

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            return mock_process

        async def _fake_filter(_src, _dst):
            return None

        stack = ExitStack()
        stack.enter_context(
            patch(
                "openhands.sdk.agent.acp_agent.asyncio.create_subprocess_exec",
                new=_fake_create_subprocess_exec,
            )
        )
        stack.enter_context(
            patch(
                "openhands.sdk.agent.acp_agent.ClientSideConnection",
                return_value=conn,
            )
        )
        stack.enter_context(
            patch(
                "openhands.sdk.agent.acp_agent._filter_jsonrpc_lines",
                new=_fake_filter,
            )
        )
        stack.enter_context(
            patch(
                "openhands.sdk.agent.acp_agent.asyncio.StreamReader",
                return_value=MagicMock(),
            )
        )
        return stack

    @staticmethod
    def _patched_start_acp_server(agent, state, *, conn):
        """Invoke the real _start_acp_server with ACP transport layers mocked."""
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent._executor = AsyncExecutor()
        with TestACPSessionIdPersistence._transport_patches(conn):
            agent._start_acp_server(state)

    @staticmethod
    def _make_conn(
        *,
        new_session_id: str = "sess-new",
        load_exc: Exception | None = None,
    ):
        conn = MagicMock()

        init_response = MagicMock()
        init_response.agent_info = MagicMock()
        init_response.agent_info.name = "claude-agent-acp"
        init_response.agent_info.version = "1.0"
        init_response.auth_methods = []
        conn.initialize = AsyncMock(return_value=init_response)

        new_response = MagicMock()
        new_response.session_id = new_session_id
        conn.new_session = AsyncMock(return_value=new_response)

        if load_exc is not None:
            conn.load_session = AsyncMock(side_effect=load_exc)
        else:
            conn.load_session = AsyncMock(return_value=MagicMock())

        conn.set_session_mode = AsyncMock()
        conn.set_session_model = AsyncMock()
        conn.authenticate = AsyncMock()
        conn.close = AsyncMock()
        return conn

    def test_fresh_state_has_no_session_id(self, tmp_path):
        """A fresh ConversationState holds no session id under agent_state."""
        state = _make_state(tmp_path)
        assert "acp_session_id" not in state.agent_state

    def test_first_launch_calls_new_session(self, tmp_path):
        """Empty agent_state → _start_acp_server calls new_session only."""
        agent = _make_agent()
        state = _make_state(tmp_path)
        conn = self._make_conn(new_session_id="fresh-sess")

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.new_session.assert_awaited_once()
        conn.load_session.assert_not_awaited()
        assert agent._session_id == "fresh-sess"

    def test_init_state_writes_session_id_into_agent_state(self, tmp_path):
        """init_state lands the session id in state.agent_state so
        ConversationState's base_state.json persistence carries it forward.
        """
        agent = _make_agent()
        state = _make_state(tmp_path)

        # Short-circuit _start_acp_server: pretend the ACP handshake ran and
        # populated the runtime attrs that init_state reads afterwards.
        def _fake_start(self, _state):
            self._session_id = "end-to-end-sess"
            self._agent_name = "claude-agent-acp"
            self._agent_version = "1.0"

        with patch.object(ACPAgent, "_start_acp_server", _fake_start):
            agent.init_state(state, on_event=lambda _: None)

        assert state.agent_state["acp_session_id"] == "end-to-end-sess"
        assert state.agent_state["acp_agent_name"] == "claude-agent-acp"
        assert state.agent_state["acp_agent_version"] == "1.0"

    def test_resume_reads_session_id_from_agent_state(self, tmp_path):
        """Prior session id in agent_state → load_session is called with it."""
        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {**state.agent_state, "acp_session_id": "stored-sess"}
        conn = self._make_conn()

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        _, kwargs = conn.load_session.call_args
        assert kwargs["session_id"] == "stored-sess"
        assert kwargs["cwd"] == str(tmp_path)
        conn.new_session.assert_not_awaited()
        assert agent._session_id == "stored-sess"

    def test_load_session_failure_falls_back_to_new_session(self, tmp_path):
        """ACPRequestError on load_session → new_session is called."""
        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {**state.agent_state, "acp_session_id": "stale-sess"}
        conn = self._make_conn(
            new_session_id="replacement-sess",
            load_exc=ACPRequestError(-32602, "unknown session"),
        )

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        conn.new_session.assert_awaited_once()
        assert agent._session_id == "replacement-sess"

    def test_session_id_not_on_serialized_agent(self):
        """Session id must not leak onto the agent model — it lives in
        ConversationState.agent_state, not on the frozen ACPAgent.
        """
        agent = _make_agent()
        data = json.loads(agent.model_dump_json())
        assert "acp_session_id" not in data
        assert not hasattr(agent, "acp_session_id")

    def test_init_state_writes_cwd_alongside_session_id(self, tmp_path):
        """init_state records the cwd the session was created under so a later
        resume can reject cwd mismatches (ACP keys persistence by cwd).
        """
        agent = _make_agent()
        state = _make_state(tmp_path)

        def _fake_start(self, _state):
            self._session_id = "sess-123"
            self._agent_name = "claude-agent-acp"
            self._agent_version = "1.0"
            self._working_dir = str(tmp_path)

        with patch.object(ACPAgent, "_start_acp_server", _fake_start):
            agent.init_state(state, on_event=lambda _: None)

        assert state.agent_state["acp_session_id"] == "sess-123"
        assert state.agent_state["acp_session_cwd"] == str(tmp_path)

    def test_cwd_mismatch_skips_load_and_calls_new_session(self, tmp_path, caplog):
        """If the stored cwd differs from the current workspace cwd, resume
        is skipped and new_session runs instead — so we never silently load
        a session that the ACP server associated with a different directory.
        """
        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "old-sess",
            "acp_session_cwd": "/some/other/place",
        }
        conn = self._make_conn(new_session_id="fresh-sess")

        with caplog.at_level("WARNING"):
            self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_not_awaited()
        conn.new_session.assert_awaited_once()
        assert agent._session_id == "fresh-sess"
        assert any(
            "cwd=/some/other/place" in rec.message and "differs" in rec.message
            for rec in caplog.records
        ), "expected a warning explaining the cwd mismatch"

    def test_resume_without_stored_cwd_still_works(self, tmp_path):
        """Legacy state written by an earlier version has acp_session_id but
        no acp_session_cwd — resume should still proceed (best-effort).
        """
        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {**state.agent_state, "acp_session_id": "legacy-sess"}
        conn = self._make_conn()

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        conn.new_session.assert_not_awaited()
        assert agent._session_id == "legacy-sess"

    def test_fallback_replacement_id_lands_in_agent_state(self, tmp_path):
        """When load_session fails and new_session runs, init_state must
        overwrite state.agent_state['acp_session_id'] with the new id so
        the next restart doesn't keep trying to resume the stale one.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "stale-sess",
            "acp_session_cwd": str(tmp_path),
        }
        conn = self._make_conn(
            new_session_id="replacement-sess",
            load_exc=ACPRequestError(-32602, "unknown session"),
        )

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        conn.load_session.assert_awaited_once()
        conn.new_session.assert_awaited_once()
        assert state.agent_state["acp_session_id"] == "replacement-sess"
        assert state.agent_state["acp_session_cwd"] == str(tmp_path)

    def test_resume_path_still_applies_session_mode_and_model(self, tmp_path):
        """load_session must be followed by the same set_session_model and
        set_session_mode calls as new_session, so a resumed session honours
        acp_model overrides and the bypass-permissions mode.
        """
        agent = _make_agent(acp_model="claude-opus-4-6")
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "stored-sess",
            "acp_session_cwd": str(tmp_path),
        }
        # Name the server "codex-acp" so _maybe_set_session_model routes
        # acp_model through conn.set_session_model (claude-acp uses _meta,
        # which only applies on new_session and so wouldn't exercise the
        # protocol-level override on the resume path).
        conn = self._make_conn()
        conn.initialize.return_value.agent_info.name = "codex-acp"
        conn.initialize.return_value.auth_methods = []

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        conn.new_session.assert_not_awaited()
        conn.set_session_model.assert_awaited_once_with(
            model_id="claude-opus-4-6",
            session_id="stored-sess",
        )
        conn.set_session_mode.assert_awaited_once_with(
            mode_id="full-access",
            session_id="stored-sess",
        )

    def test_roundtrip_via_conversation_state_persistence(self, tmp_path):
        """End-to-end round-trip through ConversationState persistence:

        1. First Conversation with persistence_dir → init_state runs,
           new_session is called, ``state.agent_state["acp_session_id"]`` is
           written, autosave flushes ``base_state.json`` to disk.
        2. Fresh ACPAgent + Conversation pointed at the same persistence_dir
           and id → ConversationState.create() restores ``base_state.json``
           so ``agent_state["acp_session_id"]`` survives; init_state on the
           resumed state triggers ``load_session`` with that id.
        """
        import uuid as _uuid

        from openhands.sdk.conversation import Conversation
        from openhands.sdk.utils.async_executor import AsyncExecutor

        persistence_dir = tmp_path / "persist"
        conv_id = _uuid.uuid4()
        workspace = tmp_path / "work"
        workspace.mkdir()

        conn1 = self._make_conn(new_session_id="roundtrip-sess")
        agent1 = _make_agent()
        agent1._executor = AsyncExecutor()
        with self._transport_patches(conn1):
            conv1 = Conversation(
                agent=agent1,
                workspace=str(workspace),
                persistence_dir=str(persistence_dir),
                conversation_id=conv_id,
                delete_on_close=False,
                visualizer=None,
            )
            conv1._ensure_agent_ready()
            assert conv1.state.agent_state["acp_session_id"] == "roundtrip-sess"
            conv1.close()

        conn1.new_session.assert_awaited_once()
        conn1.load_session.assert_not_awaited()

        # Fresh ACPAgent with no runtime knowledge of the prior session.
        conn2 = self._make_conn()
        agent2 = _make_agent()
        agent2._executor = AsyncExecutor()
        with self._transport_patches(conn2):
            conv2 = Conversation(
                agent=agent2,
                workspace=str(workspace),
                persistence_dir=str(persistence_dir),
                conversation_id=conv_id,
                delete_on_close=True,
                visualizer=None,
            )
            conv2._ensure_agent_ready()
            # base_state.json restored the id into agent_state.
            assert conv2.state.agent_state["acp_session_id"] == "roundtrip-sess"
            conv2.close()

        # Second launch took the load_session branch with the persisted id.
        conn2.load_session.assert_awaited_once()
        _, kwargs = conn2.load_session.call_args
        assert kwargs["session_id"] == "roundtrip-sess"
        assert kwargs["cwd"] == str(workspace)
        conn2.new_session.assert_not_awaited()
        assert agent2._session_id == "roundtrip-sess"


class TestACPSecretsEnvInjection:
    """Tests for secret injection into the ACP subprocess environment.

    Secrets passed via ``agent_context.secrets`` must land in the subprocess
    env so the ACP server (Claude Code, Codex CLI, etc.) can use them.
    ``acp_env`` entries take precedence over agent_context secrets.
    """

    @staticmethod
    def _make_conn():
        conn = MagicMock()
        init_response = MagicMock()
        init_response.agent_info = MagicMock()
        init_response.agent_info.name = "claude-agent-acp"
        init_response.agent_info.version = "1.0"
        init_response.auth_methods = []
        conn.initialize = AsyncMock(return_value=init_response)
        new_response = MagicMock()
        new_response.session_id = "sess-1"
        conn.new_session = AsyncMock(return_value=new_response)
        conn.load_session = AsyncMock(return_value=MagicMock())
        conn.set_session_mode = AsyncMock()
        conn.set_session_model = AsyncMock()
        conn.authenticate = AsyncMock()
        conn.close = AsyncMock()
        return conn

    @staticmethod
    def _run_start_capturing_env(agent, tmp_path) -> dict:
        """Run _start_acp_server and return the env dict passed to the subprocess."""
        from contextlib import ExitStack
        from openhands.sdk.utils.async_executor import AsyncExecutor

        captured: dict = {}
        conn = TestACPSecretsEnvInjection._make_conn()

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()

        async def _fake_create_subprocess_exec(*_args, env=None, **_kwargs):
            captured.update(env or {})
            return mock_process

        async def _fake_filter(_src, _dst):
            return None

        state = _make_state(tmp_path)
        agent._executor = AsyncExecutor()

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "openhands.sdk.agent.acp_agent.asyncio.create_subprocess_exec",
                    new=_fake_create_subprocess_exec,
                )
            )
            stack.enter_context(
                patch(
                    "openhands.sdk.agent.acp_agent.ClientSideConnection",
                    return_value=conn,
                )
            )
            stack.enter_context(
                patch(
                    "openhands.sdk.agent.acp_agent._filter_jsonrpc_lines",
                    new=_fake_filter,
                )
            )
            stack.enter_context(
                patch(
                    "openhands.sdk.agent.acp_agent.asyncio.StreamReader",
                    return_value=MagicMock(),
                )
            )
            agent._start_acp_server(state)

        return captured

    def test_static_secret_injected_into_subprocess_env(self, tmp_path):
        """A StaticSecret in agent_context.secrets lands in the subprocess env."""
        from openhands.sdk.secret import StaticSecret
        from pydantic import SecretStr

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={
                    "GITHUB_TOKEN": StaticSecret(
                        value=SecretStr("ghp_test123"),
                        description="GitHub token",
                    )
                }
            )
        )
        env = self._run_start_capturing_env(agent, tmp_path)
        assert env.get("GITHUB_TOKEN") == "ghp_test123"

    def test_acp_env_takes_precedence_over_agent_context_secret(self, tmp_path):
        """An explicit acp_env entry wins over the same key in agent_context.secrets."""
        from openhands.sdk.secret import StaticSecret
        from pydantic import SecretStr

        agent = _make_agent(
            acp_env={"MY_TOKEN": "acp-env-wins"},
            agent_context=AgentContext(
                secrets={"MY_TOKEN": StaticSecret(value=SecretStr("secret-panel"))}
            ),
        )
        env = self._run_start_capturing_env(agent, tmp_path)
        assert env.get("MY_TOKEN") == "acp-env-wins"

    def test_none_value_secret_not_injected(self, tmp_path):
        """A StaticSecret with value=None is not added to the subprocess env."""
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={"ABSENT_SECRET": StaticSecret(value=None)}
            )
        )
        env = self._run_start_capturing_env(agent, tmp_path)
        assert "ABSENT_SECRET" not in env
