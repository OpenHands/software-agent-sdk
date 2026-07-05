"""Tests for ACPAgent."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Mapping
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from acp.exceptions import RequestError as ACPRequestError
from acp.schema import NewSessionResponse, PromptResponse
from pydantic import SecretStr

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    _acp_error_detail,
    _acp_error_indicates_auth,
    _apply_acp_model,
    _classify_acp_init_error,
    _classify_acp_turn_error,
    _codex_auth_file,
    _codex_base_url_overrides,
    _codex_model_config_options,
    _estimate_cost_from_tokens,
    _extract_session_models,
    _extract_token_usage,
    _image_url_to_acp_block,
    _mask_json_value,
    _maybe_set_session_model,
    _mcp_config_to_acp_servers,
    _OpenHandsACPBridge,
    _reapply_session_model_on_resume,
    _select_auth_method,
    _serialize_tool_content,
    _stringify_acp_error_data,
    _strip_inherited_npm_env,
)
from openhands.sdk.agent.acp_models import ACPModelInfo
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import (
    ACPToolCallEvent,
    ActionEvent,
    MessageEvent,
    SystemPromptEvent,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.mcp.config import coerce_mcp_config
from openhands.sdk.secret import SecretSource
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE
from openhands.sdk.workspace.local import LocalWorkspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeLookupSecret(SecretSource):
    """Module-level stand-in for ``LookupSecret`` used by registry tests.

    Defined at module scope (not inside a test method) so its ``__qualname__``
    does not contain ``<locals>``. ``DiscriminatedUnionMixin`` rejects
    subclasses whose qualname contains ``<locals>`` during ``SecretSource``
    union validation, and any such local subclass leaks into the global
    ``__subclasses__`` registry — breaking unrelated serialization tests
    that run later on the same xdist worker.
    """

    stored_value: str

    def get_value(self) -> str | None:
        return self.stored_value


def _make_agent(**kwargs) -> ACPAgent:
    if isinstance(kwargs.get("mcp_config"), dict):
        mcp_config = kwargs["mcp_config"]
        servers = mcp_config.get("mcpServers", mcp_config)
        kwargs["mcp_config"] = coerce_mcp_config(servers)
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

    def test_strip_inherited_npm_env_keeps_subprocesses_out_of_parent_context(self):
        env = {
            "INIT_CWD": "/repo",
            "npm_config_prefix": "/repo",
            "npm_package_name": "parent-package",
            "NPM_CONFIG_PREFIX": "/repo",
            "PATH": "/usr/bin",
            "USER_VALUE": "kept",
        }

        _strip_inherited_npm_env(env)

        assert env == {
            "PATH": "/usr/bin",
            "USER_VALUE": "kept",
        }

    def test_requires_acp_command(self):
        with pytest.raises(Exception):
            ACPAgent()  # type: ignore[call-arg]

    def test_acp_command_stored(self):
        agent = ACPAgent(acp_command=["npx", "-y", "claude-agent-acp"])
        assert agent.acp_command == ["npx", "-y", "claude-agent-acp"]

    def test_acp_args_default_empty(self):
        agent = _make_agent()
        assert agent.acp_args == []

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
        )
        dumped = agent.model_dump_json()
        restored = AgentBase.model_validate_json(dumped)
        assert isinstance(restored, ACPAgent)
        assert restored.acp_command == agent.acp_command
        assert restored.acp_args == agent.acp_args

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

    def test_allows_mcp_config(self, tmp_path):
        """mcp_config is forwarded to the ACP subprocess, not rejected.

        The servers are translated and passed to new_session/load_session
        (see test_acp_mcp.py); here we just assert init_state no longer raises.
        """
        agent = ACPAgent(
            acp_command=["echo"],
            mcp_config=coerce_mcp_config({"test": {"command": "echo"}}),
        )
        # Should not raise; ACP receives MCP servers at session creation instead
        # of OpenHands creating in-process runtime MCP tools.
        self._init_with_patches(agent, tmp_path)
        assert agent.supports_openhands_tools is False

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
        # Assembled by the same prompt registry the general agent uses, so the
        # rendered dynamic-tier sections are identical.
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
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

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

        blocks = agent._build_acp_prompt(event)

        assert blocks is not None
        texts = [b.text for b in blocks if hasattr(b, "text")]
        assert "First block." in texts
        assert "Second block." in texts
        assert sum(1 for t in texts if t == "Prefer concise responses.") == 1

    def test_build_acp_prompt_includes_image_content(self):
        agent = _make_agent()
        event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[
                    TextContent(text="What is in this image?"),
                    ImageContent(image_urls=["data:image/png;base64,iVBOR"]),
                ],
            ),
        )

        blocks = agent._build_acp_prompt(event)

        assert blocks is not None
        assert len(blocks) == 2
        assert blocks[0].type == "text"
        assert blocks[0].text == "What is in this image?"
        assert blocks[1].type == "image"
        assert blocks[1].data == "iVBOR"
        assert blocks[1].mime_type == "image/png"


class TestImageUrlToAcpBlock:
    def test_data_uri(self):
        block = _image_url_to_acp_block("data:image/jpeg;base64,/9j/4AAQ")
        assert block is not None
        assert block.data == "/9j/4AAQ"
        assert block.mime_type == "image/jpeg"

    def test_plain_url(self):
        block = _image_url_to_acp_block("https://example.com/img.png")
        assert block is not None
        assert block.uri == "https://example.com/img.png"

    def test_invalid_data_uri_returns_none(self):
        block = _image_url_to_acp_block("data:broken")
        assert block is None

    def test_real_png_round_trips(self):
        """Verify a real PNG image survives the full conversion path."""
        import base64
        import struct
        import zlib

        # Minimal valid 1x1 red PNG
        sig = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
        ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
        raw = zlib.compress(b"\x00\xff\x00\x00")
        idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
        idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)
        iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
        iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
        png_bytes = sig + ihdr + idat + iend

        b64_data = base64.b64encode(png_bytes).decode()
        data_uri = f"data:image/png;base64,{b64_data}"

        block = _image_url_to_acp_block(data_uri)
        assert block is not None
        assert block.mime_type == "image/png"
        decoded = base64.b64decode(block.data)
        assert decoded == png_bytes
        assert decoded[:4] == b"\x89PNG"


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

    def test_init_state_no_dynamic_context_without_agent_context(self, tmp_path):
        agent = _make_agent()
        state = _make_state(tmp_path)
        events: list = []

        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=events.append)

        assert events[0].dynamic_context is None

    def test_init_state_populates_dynamic_context_from_suffix(self, tmp_path):
        agent = _make_agent(
            agent_context=AgentContext(system_message_suffix="Team rules.")
        )
        state = _make_state(tmp_path)
        events: list = []

        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=events.append)

        assert events[0].dynamic_context is not None
        assert "Team rules." in events[0].dynamic_context.text

    def test_init_state_sets_pending_state_for_new_session(self, tmp_path):
        agent = _make_agent(
            agent_context=AgentContext(system_message_suffix="Team rules.")
        )
        state = _make_state(tmp_path)

        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=lambda _: None)

        assert agent._suffix_install_state == "pending_first_prompt"
        assert agent._installed_suffix is not None
        assert "Team rules." in agent._installed_suffix

    def test_init_state_sets_installed_when_suffix_marker_persisted(self, tmp_path):
        """A successful first turn persists ``acp_suffix_installed`` — on resume
        the ACPAgent reads that marker and skips re-injection."""
        agent = _make_agent(
            agent_context=AgentContext(system_message_suffix="Team rules.")
        )
        state = _make_state(tmp_path)
        state.agent_state = {
            "acp_session_id": "prior-session-id",
            "acp_suffix_installed": True,
        }

        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=lambda _: None)

        assert agent._suffix_install_state == "installed"

    def test_init_state_pending_when_session_id_only_no_suffix_marker(self, tmp_path):
        """Persisted ``acp_session_id`` without ``acp_suffix_installed`` means
        the prior session was created but its first prompt never completed
        (cancelled / crashed before ``_finalize_successful_turn``).  The
        ACP subprocess never received the suffix; on resume we must
        re-inject it on the next turn rather than infer "installed" from
        session-id presence alone.
        """
        agent = _make_agent(
            agent_context=AgentContext(system_message_suffix="Team rules.")
        )
        state = _make_state(tmp_path)
        state.agent_state = {"acp_session_id": "prior-session-id"}

        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=lambda _: None)

        assert agent._suffix_install_state == "pending_first_prompt"

    def test_init_state_includes_registry_secrets_in_suffix(self, tmp_path):
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(agent_context=AgentContext(current_datetime=None))
        state = _make_state(tmp_path)
        state.secret_registry.update_secrets(
            {
                "REGISTRY_TOKEN": StaticSecret(
                    value=SecretStr("tok"), description="Registry token"
                )
            }
        )
        events: list = []

        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=events.append)

        assert events[0].dynamic_context is not None
        assert "REGISTRY_TOKEN" in events[0].dynamic_context.text

    def test_init_state_renders_registry_secrets_without_agent_context(self, tmp_path):
        """The <CUSTOM_SECRETS> block should render from secret_registry alone.

        Callers that ship secrets through the canonical conversation
        channel (``StartConversationRequest.secrets`` →
        ``Conversation.update_secrets`` → ``secret_registry``) but don't
        attach an ``AgentContext`` shouldn't see those secrets silently
        dropped from the system suffix — the values still flow into the
        subprocess env via ``_start_acp_server``, so the agent needs to
        know they're available.
        """
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()  # no agent_context
        state = _make_state(tmp_path)
        state.secret_registry.update_secrets(
            {
                "REGISTRY_TOKEN": StaticSecret(
                    value=SecretStr("tok"), description="Registry token"
                )
            }
        )
        events: list = []

        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=events.append)

        assert events[0].dynamic_context is not None
        assert "REGISTRY_TOKEN" in events[0].dynamic_context.text

    # -- Cold-start error surfacing (issue #1024) --------------------------

    def _init_state_failure(self, tmp_path, exc: BaseException):
        """Run init_state with ``_start_acp_server`` raising ``exc``.

        Returns ``(state, events, raised)`` where ``raised`` is the exception
        that escaped init_state (asserted to be the original ``exc``).
        """
        agent = _make_agent()
        state = _make_state(tmp_path)
        events: list = []
        with patch(
            "openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server",
            side_effect=exc,
        ):
            with pytest.raises(type(exc)) as excinfo:
                agent.init_state(state, on_event=events.append)
        assert excinfo.value is exc, "original exception must propagate unchanged"
        return state, events

    def test_init_state_surfaces_auth_required(self, tmp_path):
        """An auth-required protocol error becomes a typed ACPAuthRequired event
        with ERROR status — instead of bypassing emission and reaching the
        client as a generic "remote conversation ended with error"."""
        exc = ACPRequestError(-32000, "Authentication required")
        state, events = self._init_state_failure(tmp_path, exc)

        errors = [e for e in events if isinstance(e, ConversationErrorEvent)]
        assert len(errors) == 1
        assert errors[0].source == "agent"
        assert errors[0].code == "ACPAuthRequired"
        assert "Authentication required" in errors[0].detail
        assert state.execution_status == ConversationExecutionStatus.ERROR

    def test_init_state_surfaces_spawn_error(self, tmp_path):
        """A missing/unexecutable CLI binary (FileNotFoundError /
        PermissionError from create_subprocess_exec) becomes ACPSpawnError."""
        for exc in (
            FileNotFoundError("no such file: claude-agent-acp"),
            PermissionError("permission denied"),
        ):
            state, events = self._init_state_failure(tmp_path, exc)
            errors = [e for e in events if isinstance(e, ConversationErrorEvent)]
            assert len(errors) == 1
            assert errors[0].code == "ACPSpawnError"
            assert state.execution_status == ConversationExecutionStatus.ERROR

    def test_init_state_surfaces_generic_init_error(self, tmp_path):
        """Any other handshake/session failure falls back to ACPInitError."""
        exc = RuntimeError("protocol handshake timed out")
        state, events = self._init_state_failure(tmp_path, exc)

        errors = [e for e in events if isinstance(e, ConversationErrorEvent)]
        assert len(errors) == 1
        assert errors[0].code == "ACPInitError"
        assert "protocol handshake timed out" in errors[0].detail
        assert state.execution_status == ConversationExecutionStatus.ERROR

    def test_init_state_surfaces_request_error_data(self, tmp_path):
        """A ``RequestError`` cold-start failure surfaces its ``.code`` and
        ``.data`` — not the bare "Internal error" that ``str(exc)`` yields — so the
        real cause (an auth marker here) reaches the user and is classified."""
        exc = ACPRequestError(
            -32603, "Internal error", {"message": "401 Unauthorized from provider"}
        )
        _state, events = self._init_state_failure(tmp_path, exc)
        errors = [e for e in events if isinstance(e, ConversationErrorEvent)]
        assert len(errors) == 1
        assert errors[0].code == "ACPAuthRequired"
        assert "401 Unauthorized from provider" in errors[0].detail
        assert "-32603" in errors[0].detail

    def test_init_state_truncates_long_detail(self, tmp_path):
        """detail is capped at 500 chars, matching the run-loop error path."""
        exc = RuntimeError("x" * 1000)
        _state, events = self._init_state_failure(tmp_path, exc)
        errors = [e for e in events if isinstance(e, ConversationErrorEvent)]
        assert len(errors[0].detail) == 500

    def test_init_state_reraises_even_if_emission_fails(self, tmp_path):
        """A failure while surfacing the error must never mask the original
        exception — the re-raise contract run()/arun() rely on is preserved."""
        agent = _make_agent()
        state = _make_state(tmp_path)
        exc = RuntimeError("boom")

        def _explode(_event):
            raise ValueError("on_event is broken")

        with patch(
            "openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server",
            side_effect=exc,
        ):
            with pytest.raises(RuntimeError, match="boom") as excinfo:
                agent.init_state(state, on_event=_explode)
        assert excinfo.value is exc


# ---------------------------------------------------------------------------
# _classify_acp_init_error
# ---------------------------------------------------------------------------


class TestClassifyACPInitError:
    def test_auth_required_code(self):
        exc = ACPRequestError(-32000, "Authentication required")
        assert _classify_acp_init_error(exc) == "ACPAuthRequired"

    def test_other_request_error_is_init_error(self):
        # A protocol error that isn't auth-required (e.g. internal error) is a
        # generic init failure, not an auth failure.
        exc = ACPRequestError(-32603, "Internal error")
        assert _classify_acp_init_error(exc) == "ACPInitError"

    def test_file_not_found_is_spawn_error(self):
        assert _classify_acp_init_error(FileNotFoundError()) == "ACPSpawnError"

    def test_permission_error_is_spawn_error(self):
        assert _classify_acp_init_error(PermissionError()) == "ACPSpawnError"

    def test_broken_pipe_is_init_error(self):
        # A transport drop during the handshake is an OSError subclass but not a
        # spawn failure — it must classify as ACPInitError, not ACPSpawnError.
        assert _classify_acp_init_error(BrokenPipeError()) == "ACPInitError"

    def test_generic_exception_is_init_error(self):
        assert _classify_acp_init_error(RuntimeError("x")) == "ACPInitError"

    def test_auth_marker_in_internal_error_is_auth_required(self):
        # Servers collapse upstream 401/403s into a generic -32603 instead of
        # -32000; the message/data still reveal the credential failure, which must
        # map to ACPAuthRequired so the client can offer re-auth.
        exc = ACPRequestError(
            -32603, "Internal error", {"message": "401 Unauthorized: invalid api key"}
        )
        assert _classify_acp_init_error(exc) == "ACPAuthRequired"

    def test_benign_internal_error_stays_init_error(self):
        # A -32603 with no auth marker is a generic init failure, not auth.
        exc = ACPRequestError(-32603, "Internal error", {"message": "disk full"})
        assert _classify_acp_init_error(exc) == "ACPInitError"


# ---------------------------------------------------------------------------
# _stringify_acp_error_data
# ---------------------------------------------------------------------------


class TestStringifyACPErrorData:
    def test_none_is_empty(self):
        assert _stringify_acp_error_data(None) == ""

    def test_bare_string_passthrough(self):
        assert _stringify_acp_error_data("OPENAI_API_KEY is not set") == (
            "OPENAI_API_KEY is not set"
        )

    def test_dict_prefers_message_key(self):
        # codex turn errors send {"message", "codex_error_info"}.
        out = _stringify_acp_error_data(
            {"message": "model not found", "codex_error_info": "gpt-9"}
        )
        assert out == "model not found (gpt-9)"

    def test_dict_details_key(self):
        # the claude SDK catch-all wraps non-RequestError exceptions as {"details"}.
        assert _stringify_acp_error_data({"details": "boom"}) == "boom"

    def test_opaque_dict_falls_back_to_json(self):
        out = _stringify_acp_error_data({"foo": "bar"})
        assert "foo" in out and "bar" in out


# ---------------------------------------------------------------------------
# _acp_error_indicates_auth
# ---------------------------------------------------------------------------


class TestACPErrorIndicatesAuth:
    def test_explicit_auth_code(self):
        assert _acp_error_indicates_auth(
            ACPRequestError(-32000, "Authentication required")
        )

    def test_marker_in_message(self):
        assert _acp_error_indicates_auth(
            ACPRequestError(-32603, "Internal error: please run /login")
        )

    def test_marker_in_data(self):
        assert _acp_error_indicates_auth(
            ACPRequestError(-32603, "Internal error", {"message": "token expired"})
        )

    def test_marker_in_plain_exception(self):
        assert _acp_error_indicates_auth(RuntimeError("HTTP 401 Unauthorized"))

    def test_no_marker(self):
        assert not _acp_error_indicates_auth(ACPRequestError(-32603, "Internal error"))
        assert not _acp_error_indicates_auth(RuntimeError("timed out"))

    def test_http_401_as_word_matches(self):
        assert _acp_error_indicates_auth(RuntimeError("HTTP 401 Unauthorized"))

    def test_digit_substring_in_timeout_does_not_match(self):
        # "4031ms" contains "403" but must not fire — word-boundary check.
        assert not _acp_error_indicates_auth(RuntimeError("timeout after 4031ms"))

    def test_model_id_with_401_does_not_match(self):
        assert not _acp_error_indicates_auth(
            ACPRequestError(
                -32603, "Internal error", {"message": "model id '401b' not found"}
            )
        )


# ---------------------------------------------------------------------------
# _classify_acp_turn_error
# ---------------------------------------------------------------------------


class TestClassifyACPTurnError:
    def test_generic_turn_error(self):
        assert _classify_acp_turn_error(RuntimeError("boom")) == "ACPPromptError"

    def test_usage_policy_refusal(self):
        exc = RuntimeError("blocked by usage policy")
        assert _classify_acp_turn_error(exc) == "UsagePolicyRefusal"

    def test_content_policy_refusal(self):
        exc = ACPRequestError(-32603, "Internal error", {"message": "content policy"})
        assert _classify_acp_turn_error(exc) == "UsagePolicyRefusal"

    def test_auth_failure_maps_to_auth_required(self):
        # A bad credential surfaced mid-turn as -32603 must still route to re-auth.
        exc = ACPRequestError(-32603, "Internal error", {"message": "401 unauthorized"})
        assert _classify_acp_turn_error(exc) == "ACPAuthRequired"


# ---------------------------------------------------------------------------
# _acp_error_detail
# ---------------------------------------------------------------------------


class TestACPErrorDetail:
    def test_plain_exception_is_str(self):
        assert _acp_error_detail(RuntimeError("boom")) == "boom"

    def test_request_error_keeps_code_and_data(self):
        # The bug this fixes: str(RequestError) is just "Internal error"; the real
        # cause lives in .code/.data and must reach the user.
        exc = ACPRequestError(
            -32603, "Internal error", {"message": "model gpt-9 not found"}
        )
        detail = _acp_error_detail(exc)
        assert "-32603" in detail
        assert "model gpt-9 not found" in detail

    def test_request_error_bare_data_string(self):
        exc = ACPRequestError(-32603, "Internal error", "OPENAI_API_KEY is not set")
        detail = _acp_error_detail(exc)
        assert "OPENAI_API_KEY is not set" in detail

    def test_no_duplicate_when_data_matches_message(self):
        exc = ACPRequestError(-32000, "Authentication required")
        detail = _acp_error_detail(exc)
        assert detail == "[-32000] Authentication required"

    def test_data_appended_when_it_differs_from_message(self):
        # data["message"] differs from exc.message → must be included.
        exc = ACPRequestError(
            -32603, "Internal error", {"message": "model gpt-9 not found"}
        )
        detail = _acp_error_detail(exc)
        assert "model gpt-9 not found" in detail
        assert "Internal error" in detail

    def test_data_not_duplicated_when_equal_to_message(self):
        # When data["message"] == exc.message, nothing extra to add — avoid
        # "[-32603] Internal error: Internal error" redundancy.
        exc = ACPRequestError(-32603, "Internal error", {"message": "Internal error"})
        detail = _acp_error_detail(exc)
        assert detail == "[-32603] Internal error"

    def test_truncated_to_500_chars(self):
        exc = ACPRequestError(-32603, "Internal error", "x" * 1000)
        assert len(_acp_error_detail(exc)) == 500

    def test_pattern_redaction_of_data(self):
        # redact_text_secrets scrubs api_key=... even without a registry.
        exc = ACPRequestError(-32603, "Internal error", "api_key='sk-leakme123'")
        detail = _acp_error_detail(exc)
        assert "sk-leakme123" not in detail
        assert "<redacted>" in detail

    def test_registry_masks_tracked_secret_value(self):
        registry = SecretRegistry()
        registry.update_secrets({"PROVIDER_TOKEN": "supersecretvalue"})
        # Resolve once so the value is tracked for masking.
        registry.get_all_secrets_as_env_vars()
        exc = ACPRequestError(
            -32603, "Internal error", {"message": "rejected supersecretvalue"}
        )
        detail = _acp_error_detail(exc, registry)
        assert "supersecretvalue" not in detail
        assert "<secret-hidden>" in detail


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
# Tool-call event emission (started + terminal, no per-progress fan-out)
# ---------------------------------------------------------------------------


def _mk_tool_start(
    tool_call_id: str = "tc-1",
    *,
    title: str = "git status",
    kind: str = "execute",
    status: str = "in_progress",
    raw_input: Any | None = None,
    raw_output: Any | None = None,
    content: Any | None = None,
) -> Any:
    from acp.schema import ToolCallStart

    start = MagicMock(spec=ToolCallStart)
    start.tool_call_id = tool_call_id
    start.title = title
    start.kind = kind
    start.status = status
    start.raw_input = raw_input
    start.raw_output = raw_output
    start.content = content
    return start


def _mk_tool_progress(
    tool_call_id: str = "tc-1",
    *,
    title: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    raw_input: Any | None = None,
    raw_output: Any | None = None,
    content: Any | None = None,
) -> Any:
    from acp.schema import ToolCallProgress

    progress = MagicMock(spec=ToolCallProgress)
    progress.tool_call_id = tool_call_id
    progress.title = title
    progress.kind = kind
    progress.status = status
    progress.raw_input = raw_input
    progress.raw_output = raw_output
    progress.content = content
    return progress


class TestACPToolCallProgressCollapse:
    """The bridge persists exactly one ``started`` + one terminal event.

    Each ``ToolCallProgress`` carries the *full cumulative* output, so emitting
    one event per frame is O(n^2) storage + WebSocket relay. The bridge instead
    streams one early ``started`` event and one terminal (``completed`` /
    ``failed``) event per ``tool_call_id`` — the action->observation pair —
    while silently accumulating the intermediate frames so the terminal event
    still carries the final output.
    """

    @pytest.mark.asyncio
    async def test_tool_call_start_emits_started_event(self) -> None:
        client = _OpenHandsACPBridge()
        events: list[Any] = []
        client.on_event = events.append

        await client.session_update("s1", _mk_tool_start(status="in_progress"))

        assert len(events) == 1
        assert isinstance(events[0], ACPToolCallEvent)
        assert events[0].tool_call_id == "tc-1"
        assert events[0].status == "in_progress"
        assert events[0].is_error is False

    @pytest.mark.asyncio
    async def test_intermediate_progress_frames_are_not_emitted(self) -> None:
        client = _OpenHandsACPBridge()
        events: list[Any] = []
        client.on_event = events.append

        await client.session_update("s1", _mk_tool_start(status="in_progress"))
        # Several non-terminal progress frames with growing cumulative output.
        for chunk in ("a", "ab", "abc"):
            await client.session_update(
                "s1", _mk_tool_progress(status="in_progress", raw_output=chunk)
            )

        # Only the started event was persisted — the intermediate frames are
        # accumulated silently (this is the O(n^2)->O(1) collapse).
        assert len(events) == 1
        assert events[0].status == "in_progress"

    @pytest.mark.asyncio
    async def test_full_lifecycle_emits_exactly_two_events(self) -> None:
        client = _OpenHandsACPBridge()
        events: list[Any] = []
        client.on_event = events.append

        await client.session_update("s1", _mk_tool_start(status="pending"))
        await client.session_update(
            "s1", _mk_tool_progress(status="in_progress", raw_output="partial")
        )
        await client.session_update(
            "s1", _mk_tool_progress(status="completed", raw_output="partial-final")
        )

        assert len(events) == 2
        started, terminal = events
        assert started.status == "pending"
        assert terminal.status == "completed"
        # Terminal event carries the final cumulative output.
        assert terminal.raw_output == "partial-final"
        assert terminal.is_error is False

    @pytest.mark.asyncio
    async def test_failed_terminal_sets_is_error(self) -> None:
        client = _OpenHandsACPBridge()
        events: list[Any] = []
        client.on_event = events.append

        await client.session_update("s1", _mk_tool_start(status="in_progress"))
        await client.session_update(
            "s1", _mk_tool_progress(status="failed", raw_output="boom")
        )

        assert len(events) == 2
        assert events[-1].status == "failed"
        assert events[-1].is_error is True

    @pytest.mark.asyncio
    async def test_single_shot_completed_start_emits_once(self) -> None:
        """A ToolCallStart that is already terminal is the only event."""
        client = _OpenHandsACPBridge()
        events: list[Any] = []
        client.on_event = events.append

        await client.session_update(
            "s1", _mk_tool_start(status="completed", raw_output="done")
        )

        assert len(events) == 1
        assert events[0].status == "completed"

    @pytest.mark.asyncio
    async def test_redundant_terminal_progress_does_not_double_emit(self) -> None:
        """Only the first transition into a terminal status emits."""
        client = _OpenHandsACPBridge()
        events: list[Any] = []
        client.on_event = events.append

        await client.session_update("s1", _mk_tool_start(status="in_progress"))
        await client.session_update("s1", _mk_tool_progress(status="completed"))
        # A trailing duplicate terminal frame must not produce a third event.
        await client.session_update("s1", _mk_tool_progress(status="completed"))

        assert len(events) == 2
        assert [e.status for e in events] == ["in_progress", "completed"]

    def test_finalize_flush_completes_orphaned_tool_calls(self) -> None:
        """A card the server opened but never closed is flushed to completed."""
        agent = _make_agent()
        client = _OpenHandsACPBridge()
        events: list[Any] = []
        client.on_event = events.append
        agent._client = client

        # One still-running call and one already-terminal call.
        client.accumulated_tool_calls.append(
            {
                "tool_call_id": "live-1",
                "title": "long task",
                "tool_kind": "execute",
                "status": "in_progress",
                "raw_input": None,
                "raw_output": "partial output",
                "content": None,
            }
        )
        client.accumulated_tool_calls.append(
            {
                "tool_call_id": "done-1",
                "title": "quick task",
                "tool_kind": "read",
                "status": "completed",
                "raw_input": None,
                "raw_output": "ok",
                "content": None,
            }
        )

        agent._flush_inflight_tool_calls_as_completed()

        # Only the non-terminal call is flushed (the terminal one is untouched).
        assert len(events) == 1
        assert events[0].tool_call_id == "live-1"
        assert events[0].status == "completed"
        assert events[0].is_error is False
        assert events[0].raw_output == "partial output"
        # The accumulator entry is now terminal so it won't be flushed again.
        assert client.accumulated_tool_calls[0]["status"] == "completed"


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

    def test_idle_clock_unarmed_reports_infinite_idle(self):
        """Before arming, the idle clock reports an unbounded gap."""
        client = _OpenHandsACPBridge()
        assert client.seconds_since_last_activity() == float("inf")

    def test_arm_activity_clock_resets_idle(self):
        client = _OpenHandsACPBridge()
        client.arm_activity_clock()
        # Just armed → effectively zero seconds since activity.
        assert client.seconds_since_last_activity() < 1.0

    @pytest.mark.asyncio
    async def test_session_update_records_activity_for_idle_clock(self):
        """Every session_update resets the idle clock, even when throttled.

        The throttled heartbeat (_last_activity_signal) and the idle clock
        (_last_activity_monotonic) are independent: a second update inside the
        throttle window does not re-fire on_activity but still counts as
        progress for the idle timeout.
        """
        from acp.schema import AgentThoughtChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        client._last_activity_monotonic = float("-inf")

        # A thought chunk does not fire the on_activity heartbeat at all, but
        # must still count as activity for the idle clock.
        chunk = MagicMock(spec=AgentThoughtChunk)
        chunk.content = MagicMock(spec=TextContentBlock)
        chunk.content.text = "thinking"
        await client.session_update("sess-1", chunk)

        assert client.seconds_since_last_activity() < 1.0

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
# Prompt idle (inactivity) timeout
# ---------------------------------------------------------------------------


class TestACPPromptIdleTimeout:
    """The prompt deadline is an idle timeout: ACP activity resets it.

    Regression coverage for agent-canvas#1245 — long-running ACP prompts must
    keep working as long as the agent makes progress, rather than dying at a
    hard wall-clock deadline.
    """

    @pytest.mark.asyncio
    async def test_active_prompt_outlives_idle_window(self):
        """A prompt that keeps streaming updates is not killed at the deadline.

        The agent runs for well over ``acp_prompt_timeout`` of total wall-clock
        time, but emits a ``session_update`` far more often than the idle window,
        so the deadline keeps resetting and the prompt completes normally.
        """
        from acp.schema import AgentMessageChunk, TextContentBlock

        agent = _make_agent(acp_prompt_timeout=0.3)
        client = _OpenHandsACPBridge()
        agent._client = client
        client.arm_activity_clock()

        sentinel = object()

        async def _active_prompt() -> Any:
            # ~0.5s total (> 0.3s idle window) but a tick every 0.02s
            # (<< 0.3s), so the idle clock never elapses.
            for _ in range(25):
                await asyncio.sleep(0.02)
                chunk = MagicMock(spec=AgentMessageChunk)
                chunk.content = MagicMock(spec=TextContentBlock)
                chunk.content.text = "tick"
                await client.session_update("sess-1", chunk)
            return sentinel

        result = await agent._await_with_idle_deadline(
            _active_prompt(), cancel_on_exit=True
        )
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_silent_prompt_times_out_after_idle_window(self):
        """A prompt that produces no activity is aborted after the idle window."""
        agent = _make_agent(acp_prompt_timeout=0.1)
        client = _OpenHandsACPBridge()
        agent._client = client
        client.arm_activity_clock()

        cancelled = asyncio.Event()

        async def _silent_prompt() -> Any:
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return object()

        with pytest.raises(TimeoutError, match="no activity"):
            await agent._await_with_idle_deadline(_silent_prompt(), cancel_on_exit=True)

        # The helper cancels the underlying prompt on timeout.
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_late_activity_extends_then_idle_times_out(self):
        """Activity extends the deadline; silence after it still times out."""
        from acp.schema import AgentMessageChunk, TextContentBlock

        agent = _make_agent(acp_prompt_timeout=0.15)
        client = _OpenHandsACPBridge()
        agent._client = client
        client.arm_activity_clock()

        async def _active_then_silent() -> Any:
            # One burst of activity past the first idle window...
            await asyncio.sleep(0.1)
            chunk = MagicMock(spec=AgentMessageChunk)
            chunk.content = MagicMock(spec=TextContentBlock)
            chunk.content.text = "tick"
            await client.session_update("sess-1", chunk)
            # ...then go silent so the (extended) idle window elapses.
            await asyncio.sleep(5.0)
            return object()

        with pytest.raises(TimeoutError, match="no activity"):
            await agent._await_with_idle_deadline(
                _active_then_silent(), cancel_on_exit=True
            )


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

    def test_step_emits_finish_action_event(self, tmp_path):
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

        # step() emits ActionEvent(FinishAction) + ObservationEvent(FinishObservation)
        # MessageEvent is not emitted — FinishAction.message carries the response text
        assert len(events) == 2
        assert isinstance(events[0], ActionEvent)
        assert isinstance(events[0].action, FinishAction)
        assert events[0].action.message == "The answer is 4"

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
        assert agent.agent_context is not None
        agent._installed_suffix = agent.agent_context.to_acp_prompt_context()
        agent._suffix_install_state = "pending_first_prompt"

        agent.step(conversation, on_event=lambda _: None)

        prompt_call = agent._conn.prompt.await_args
        assert prompt_call is not None
        prompt_blocks = prompt_call.args[0]
        prompt_text = "\n\n".join(b.text for b in prompt_blocks if hasattr(b, "text"))
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
        assert agent.agent_context is not None
        agent._installed_suffix = agent.agent_context.to_acp_prompt_context()
        agent._suffix_install_state = "pending_first_prompt"

        agent.step(conversation, on_event=lambda _: None)

        prompt_call = agent._conn.prompt.await_args
        assert prompt_call is not None
        prompt_text = "\n\n".join(
            b.text for b in prompt_call.args[0] if hasattr(b, "text")
        )
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
        assert agent.agent_context is not None
        agent._installed_suffix = agent.agent_context.to_acp_prompt_context()
        agent._suffix_install_state = "pending_first_prompt"

        agent.step(conversation, on_event=lambda _: None)

        prompt_call = agent._conn.prompt.await_args
        assert prompt_call is not None
        prompt_text = "\n\n".join(
            b.text for b in prompt_call.args[0] if hasattr(b, "text")
        )
        assert "Legacy triggered review instructions." in prompt_text
        assert "AgentSkills triggered review instructions." in prompt_text
        assert "<name>agentskill-review</name>" in prompt_text
        assert "<description>AgentSkills review catalog.</description>" in prompt_text

    def test_step_does_not_re_inject_suffix_on_second_turn(self, tmp_path):
        """Suffix must not appear in subsequent turns after the first injection."""
        agent = _make_agent(
            agent_context=AgentContext(
                system_message_suffix="Team rules.", current_datetime=None
            )
        )
        state = _make_state(tmp_path)
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="Turn 2.")]),
            )
        )
        conversation = MagicMock()
        conversation.state = state
        self._wire_passthrough_mocks(agent)
        # Simulate: suffix was already installed on the first turn.
        agent._installed_suffix = agent.agent_context.to_acp_prompt_context()  # type: ignore[union-attr]
        agent._suffix_install_state = "installed"

        agent.step(conversation, on_event=lambda _: None)

        prompt_text = "\n\n".join(
            b.text for b in agent._conn.prompt.await_args.args[0] if hasattr(b, "text")
        )
        assert "Team rules." not in prompt_text

    def test_step_suffix_install_state_transitions_to_installed(self, tmp_path):
        """After the first turn the install state must be 'installed' AND the
        ``acp_suffix_installed`` marker must be persisted into
        ``state.agent_state`` so a subsequent agent-server restart can tell
        the suffix was actually installed (rather than inferring from the
        mere presence of ``acp_session_id``)."""
        agent = _make_agent(
            agent_context=AgentContext(
                system_message_suffix="Team rules.", current_datetime=None
            )
        )
        state = _make_state(tmp_path)
        state.events.append(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="First.")]),
            )
        )
        conversation = MagicMock()
        conversation.state = state
        self._wire_passthrough_mocks(agent)
        agent._installed_suffix = agent.agent_context.to_acp_prompt_context()  # type: ignore[union-attr]
        agent._suffix_install_state = "pending_first_prompt"

        agent.step(conversation, on_event=lambda _: None)

        assert agent._suffix_install_state == "installed"
        assert state.agent_state.get("acp_suffix_installed") is True

    def test_step_with_reasoning_surfaces_via_action_event(self, tmp_path):
        """Reasoning traces are preserved in ActionEvent.reasoning_content."""
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

        assert isinstance(events[0], ActionEvent)
        assert isinstance(events[0].action, FinishAction)
        assert events[0].action.message == "4"
        assert events[0].reasoning_content == "I need to add 2+2"

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

        assert isinstance(events[0], ActionEvent)
        assert isinstance(events[0].action, FinishAction)
        assert "(No response from ACP server)" in events[0].action.message

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
# Async step (astep) — regression coverage for #3348
# ---------------------------------------------------------------------------


class TestACPAgentAstep:
    """Native ``ACPAgent.astep`` must not fall back to ``AgentBase.astep``
    (which wraps ``step`` in ``loop.run_in_executor``).  Doing so would
    move post-prompt callbacks and state updates onto an executor worker
    thread, outside ``LocalConversation.arun``'s controlled event
    serialization. See #3348.
    """

    def _make_conversation_with_message(self, tmp_path, text="Hello"):
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

    def test_astep_overrides_default_agentbase_implementation(self):
        """Structural guard: if this flips back, ``AgentBase.astep``'s
        ``run_in_executor`` wrapper resumes and #3348 reopens.
        """
        assert ACPAgent.astep is not AgentBase.astep

    def test_astep_runs_post_prompt_callbacks_on_caller_thread(self, tmp_path):
        """Post-prompt ``on_event`` callbacks must fire on the caller
        thread. If astep schedules ``step`` on a worker thread (the buggy
        default), callbacks and final state updates run outside the async
        run task's serialization model — see #3348.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        caller_thread_id = threading.get_ident()
        prompt_thread_id: list[int] = []
        on_event_thread_ids: list[int] = []

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        async def _fake_prompt(prompt_blocks, session_id):
            # Must execute on the portal loop's thread, not the caller's
            # — proves we actually crossed the loop boundary.
            prompt_thread_id.append(threading.get_ident())
            mock_client.accumulated_text.append("answer")
            return None

        agent._conn.prompt = _fake_prompt
        agent._session_id = "test-session"

        executor = AsyncExecutor()
        try:
            agent._executor = executor

            def _capture_event(event):
                on_event_thread_ids.append(threading.get_ident())

            asyncio.run(agent.astep(conversation, on_event=_capture_event))
        finally:
            executor.close()

        assert len(prompt_thread_id) == 1
        assert prompt_thread_id[0] != caller_thread_id

        # FinishAction + ObservationEvent — both on caller thread.
        assert len(on_event_thread_ids) >= 2
        for tid in on_event_thread_ids:
            assert tid == caller_thread_id, (
                f"on_event ran on thread {tid} instead of caller "
                f"{caller_thread_id} — astep regressed to thread-pool path"
            )

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )

    def test_astep_restarts_session_off_caller_loop(self, tmp_path):
        """Restart init can synchronously resolve loopback LookupSecret values.

        ``astep`` must keep that restart work off the caller loop so the
        agent-server can serve those loopback HTTP requests instead of waiting
        for each secret lookup to time out.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        emitted: list = []

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"
        agent._restart_session_on_next_turn = True

        async def _fake_prompt(prompt_blocks, session_id):  # noqa: ARG001
            mock_client.accumulated_text.append("answer")
            return None

        agent._conn.prompt = _fake_prompt

        executor = AsyncExecutor()

        async def _run_restart() -> None:
            caller_loop = asyncio.get_running_loop()
            caller_thread_id = threading.get_ident()
            restart_entered = asyncio.Event()
            release_restart = threading.Event()
            restart_thread_ids: list[int] = []

            def _blocking_restart(self, state, on_event):  # noqa: ARG001
                assert self is agent
                restart_thread_ids.append(threading.get_ident())
                caller_loop.call_soon_threadsafe(restart_entered.set)
                assert release_restart.wait(5.0)
                agent._restart_session_on_next_turn = False

            with patch.object(
                ACPAgent,
                "_restart_session_after_drain_timeout",
                new=_blocking_restart,
            ):
                task = asyncio.create_task(
                    agent.astep(conversation, on_event=emitted.append)
                )
                await asyncio.wait_for(restart_entered.wait(), timeout=5.0)
                assert len(restart_thread_ids) == 1
                assert restart_thread_ids[0] != caller_thread_id
                release_restart.set()
                await asyncio.wait_for(task, timeout=5.0)

        try:
            agent._executor = executor
            asyncio.run(_run_restart())
        finally:
            executor.close()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        assert emitted

    def test_astep_active_prompt_survives_idle_window(self, tmp_path):
        """End-to-end via the real portal: an actively-streaming prompt that
        runs well past ``acp_prompt_timeout`` finalizes normally.

        Exercises the full concurrency model — the prompt runs on the portal
        loop while the idle watchdog polls on the caller loop, and each
        bridge ``session_update`` (fired across the loop boundary) resets the
        deadline. Regression coverage for agent-canvas#1245.
        """
        from acp.schema import AgentMessageChunk, TextContentBlock

        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent(acp_prompt_timeout=0.3)
        conversation = self._make_conversation_with_message(tmp_path)
        emitted: list = []

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        async def _fake_prompt(prompt_blocks, session_id):  # noqa: ARG001
            # ~0.5s total (> 0.3s idle window), one update every 0.02s so the
            # deadline keeps resetting; then complete the turn.
            for _ in range(25):
                await asyncio.sleep(0.02)
                chunk = MagicMock(spec=AgentMessageChunk)
                chunk.content = MagicMock(spec=TextContentBlock)
                chunk.content.text = "tick"
                await mock_client.session_update(session_id, chunk)
            return None

        agent._conn.prompt = _fake_prompt

        executor = AsyncExecutor()
        try:
            agent._executor = executor
            asyncio.run(agent.astep(conversation, on_event=emitted.append))
        finally:
            executor.close()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        assert not any(
            isinstance(e, MessageEvent)
            and any(
                isinstance(c, TextContent) and "timed out" in c.text
                for c in e.llm_message.content
            )
            for e in emitted
        )

    def test_astep_emits_error_and_reraises_on_exception(self, tmp_path):
        """astep's error path must call ``_emit_turn_error`` AND re-raise.

        Guards against a silently swallowed ``raise`` in the
        ``except Exception`` branch — without re-raise,
        ``LocalConversation.arun()`` would not transition out of the
        loop and the failure would be invisible to ``RemoteConversation``.
        Mirrors the contract that sync ``step()`` already enforces.
        """
        from openhands.sdk.event.conversation_error import ConversationErrorEvent
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        emitted: list = []

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        async def _failing_prompt(prompt_blocks, session_id):
            raise RuntimeError("simulated upstream failure")

        agent._conn.prompt = _failing_prompt

        executor = AsyncExecutor()
        try:
            agent._executor = executor
            with pytest.raises(RuntimeError, match="simulated upstream failure"):
                asyncio.run(agent.astep(conversation, on_event=emitted.append))
        finally:
            executor.close()

        # _emit_turn_error emits exactly two events: MessageEvent + typed
        # ConversationErrorEvent.  Both must land before re-raise.
        def _message_text(ev: MessageEvent) -> str:
            first = ev.llm_message.content[0]
            return first.text if isinstance(first, TextContent) else ""

        error_messages = [
            e
            for e in emitted
            if isinstance(e, MessageEvent) and "ACP error" in _message_text(e)
        ]
        typed_errors = [
            e
            for e in emitted
            if isinstance(e, ConversationErrorEvent) and e.code == "ACPPromptError"
        ]
        assert len(error_messages) == 1, (
            f"expected one error MessageEvent, got {emitted}"
        )
        assert len(typed_errors) == 1, (
            f"expected one ConversationErrorEvent, got {emitted}"
        )
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

    def test_astep_times_out_when_idle_with_inflight_tool_call(self, tmp_path):
        """The idle timeout fires when a tool call hangs with no further updates.

        The deadline is an inactivity timeout: a tool card that was opened but
        then produces no further ``session_update`` (the prompt future never
        resolves and nothing streams) is silent, so the idle window elapses and
        the timeout path must cancel the ACP session and close the streamed tool
        card as failed. (Ongoing activity instead resets the clock — see
        ``TestACPPromptIdleTimeout``.)
        """
        from concurrent.futures import Future

        agent = _make_agent(acp_prompt_timeout=0.02)
        conversation = self._make_conversation_with_message(tmp_path)
        emitted: list = []
        cancel_called = threading.Event()

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        class _FakePortal:
            def __init__(self) -> None:
                self.prompt_future: Future = Future()

            def start_task_soon(self, fn, *args):  # noqa: ANN001, ANN202
                if args:
                    entry = {
                        "tool_call_id": "git-1",
                        "title": "git status",
                        "tool_kind": "execute",
                        "status": "in_progress",
                        "raw_input": None,
                        "raw_output": None,
                        "content": None,
                    }
                    mock_client.accumulated_tool_calls.append(entry)
                    mock_client._emit_tool_call_event(entry)
                    return self.prompt_future

                cancel_called.set()
                cancel_future: Future = Future()
                cancel_future.set_result(None)
                return cancel_future

        mock_executor = MagicMock()
        mock_executor.portal = _FakePortal()
        agent._executor = mock_executor

        with patch("openhands.sdk.agent.acp_agent._ACP_CANCEL_DRAIN_TIMEOUT", 0.01):
            asyncio.run(agent.astep(conversation, on_event=emitted.append))

        assert cancel_called.is_set()
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
        assert any(
            isinstance(e, ACPToolCallEvent)
            and e.tool_call_id == "git-1"
            and e.status == "failed"
            and e.is_error
            for e in emitted
        )

        def _message_text(ev: MessageEvent) -> str:
            first = ev.llm_message.content[0]
            return first.text if isinstance(first, TextContent) else ""

        assert any(
            isinstance(e, MessageEvent)
            and "ACP prompt timed out after" in _message_text(e)
            for e in emitted
        )
        assert not any(
            isinstance(e, ActionEvent) and isinstance(e.action, FinishAction)
            for e in emitted
        )

    def test_astep_emits_failed_tool_calls_on_cancellation(self, tmp_path):
        """``asyncio.CancelledError`` during astep must close in-flight
        ``ACPToolCallEvent``s as ``failed`` and re-raise.

        ``asyncio.CancelledError`` inherits from ``BaseException`` (not
        ``Exception``), so the generic ``except Exception`` handler does
        not catch it — without an explicit ``except asyncio.CancelledError``
        branch, the cancel races straight to ``finally`` (which only
        clears callbacks).  Any ``pending`` / ``in_progress`` tool cards
        already streamed would then stay live forever
        (``LocalConversation._emit_orphaned_action_errors`` only patches
        ``ActionEvent``s, not ``ACPToolCallEvent``s).
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        emitted: list = []

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        executor = AsyncExecutor()

        async def _run_with_cancel() -> None:
            prompt_entered = asyncio.Event()
            cancel_called = asyncio.Event()
            prompt_released = threading.Event()
            caller_loop = asyncio.get_running_loop()

            async def _fake_prompt(prompt_blocks, session_id):
                # Seed an in-flight tool call AFTER _reset_client_for_turn
                # has run (which clears accumulated_tool_calls).  In
                # production the bridge accumulates these inside
                # session_update as ToolCallStart / ToolCallProgress
                # notifications arrive.
                mock_client.accumulated_tool_calls.append(
                    {
                        "tool_call_id": "tc-cancel-1",
                        "title": "in-flight tool",
                        "status": "in_progress",
                        "tool_kind": None,
                        "raw_input": None,
                        "raw_output": None,
                        "content": None,
                    }
                )
                # Signal caller loop that we're holding inside the prompt
                # so the cancel races deterministically.
                caller_loop.call_soon_threadsafe(prompt_entered.set)
                # Block beyond the cancel-drain timeout so this test exercises
                # the non-quiesced cancellation path that must synthesize
                # failed ACP tool-call events.
                released = await asyncio.to_thread(prompt_released.wait, 10.0)
                assert released
                return None

            async def _fake_cancel(session_id):
                assert session_id == "test-session"
                caller_loop.call_soon_threadsafe(cancel_called.set)

            agent._conn.prompt = _fake_prompt
            agent._conn.cancel = _fake_cancel
            agent._session_id = "test-session"

            task = asyncio.create_task(
                agent.astep(conversation, on_event=emitted.append)
            )
            await asyncio.wait_for(prompt_entered.wait(), timeout=5.0)
            task.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    with patch(
                        "openhands.sdk.agent.acp_agent._ACP_CANCEL_DRAIN_TIMEOUT",
                        0.01,
                    ):
                        await task
                await asyncio.wait_for(cancel_called.wait(), timeout=5.0)
            finally:
                prompt_released.set()

        try:
            agent._executor = executor
            asyncio.run(_run_with_cancel())
        finally:
            executor.close()

        failed_tool_events = [
            e
            for e in emitted
            if isinstance(e, ACPToolCallEvent)
            and e.tool_call_id == "tc-cancel-1"
            and e.status == "failed"
        ]
        assert len(failed_tool_events) == 1, (
            f"expected one terminal failed event for tc-cancel-1, "
            f"got: {[(type(e).__name__, getattr(e, 'status', None)) for e in emitted]}"
        )
        assert failed_tool_events[0].is_error is True

    def test_astep_finalizes_and_reraises_completed_cancelled_prompt(self, tmp_path):
        """If a cancelled ACP prompt drains successfully, keep the completed turn.

        The ACP server may finish the prompt while ``session/cancel`` is being
        delivered. In that case the remote session has accepted the assistant
        turn, so OpenHands must finalize the same turn locally instead of
        discarding the response and later resuming from diverged session history.
        The original cancellation still propagates so explicit user stop intent
        wins at the conversation layer.
        """
        from acp.schema import AgentMessageChunk, TextContentBlock

        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        emitted: list = []

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        executor = AsyncExecutor()

        async def _run_with_cancel() -> None:
            prompt_entered = asyncio.Event()
            cancel_called = asyncio.Event()
            prompt_released = threading.Event()
            caller_loop = asyncio.get_running_loop()

            async def _fake_prompt(prompt_blocks, session_id):  # noqa: ARG001
                caller_loop.call_soon_threadsafe(prompt_entered.set)
                released = await asyncio.to_thread(prompt_released.wait, 10.0)
                assert released
                await mock_client.session_update(
                    session_id,
                    AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text="done"),
                    ),
                )
                return None

            async def _fake_cancel(session_id):
                assert session_id == "test-session"
                caller_loop.call_soon_threadsafe(cancel_called.set)
                prompt_released.set()

            agent._conn.prompt = _fake_prompt
            agent._conn.cancel = _fake_cancel
            agent._session_id = "test-session"

            task = asyncio.create_task(
                agent.astep(conversation, on_event=emitted.append)
            )
            await asyncio.wait_for(prompt_entered.wait(), timeout=5.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancel_called.wait(), timeout=5.0)

        try:
            agent._executor = executor
            asyncio.run(_run_with_cancel())
        finally:
            executor.close()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        assert any(
            isinstance(e, ActionEvent)
            and isinstance(e.action, FinishAction)
            and e.action.message == "done"
            for e in emitted
        )

    def test_astep_cancelled_prompt_error_pauses_without_turn_error(self, tmp_path):
        """Explicit cancellation should not emit stale prompt errors."""
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        emitted: list = []

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        executor = AsyncExecutor()

        async def _run_with_cancel() -> None:
            prompt_entered = asyncio.Event()
            cancel_called = asyncio.Event()
            prompt_released = threading.Event()
            caller_loop = asyncio.get_running_loop()

            async def _fake_prompt(prompt_blocks, session_id):  # noqa: ARG001
                caller_loop.call_soon_threadsafe(prompt_entered.set)
                released = await asyncio.to_thread(prompt_released.wait, 10.0)
                assert released
                raise RuntimeError("late prompt failure")

            async def _fake_cancel(session_id):
                assert session_id == "test-session"
                caller_loop.call_soon_threadsafe(cancel_called.set)
                prompt_released.set()

            agent._conn.prompt = _fake_prompt
            agent._conn.cancel = _fake_cancel
            agent._session_id = "test-session"

            task = asyncio.create_task(
                agent.astep(conversation, on_event=emitted.append)
            )
            await asyncio.wait_for(prompt_entered.wait(), timeout=5.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancel_called.wait(), timeout=5.0)

        try:
            agent._executor = executor
            asyncio.run(_run_with_cancel())
        finally:
            executor.close()

        assert not any(
            isinstance(e, MessageEvent)
            and e.source == "agent"
            and any(
                isinstance(c, TextContent) and c.text.startswith("ACP error:")
                for c in e.llm_message.content
            )
            for e in emitted
        )
        assert not any(isinstance(e, ConversationErrorEvent) for e in emitted)
        assert agent._restart_session_on_next_turn is True

    def test_astep_double_cancel_during_drain_restarts_next_turn(self, tmp_path):
        """A second cancellation during drain should quarantine the live prompt."""
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        executor = AsyncExecutor()

        async def _run_with_double_cancel() -> None:
            prompt_entered = asyncio.Event()
            prompt_released = threading.Event()
            caller_loop = asyncio.get_running_loop()

            async def _fake_prompt(prompt_blocks, session_id):  # noqa: ARG001
                caller_loop.call_soon_threadsafe(prompt_entered.set)
                released = await asyncio.to_thread(prompt_released.wait, 10.0)
                assert released
                return None

            async def _fake_cancel(session_id):
                assert session_id == "test-session"

            async def _raise_during_drain(self, future):  # noqa: ARG001
                assert future is not None
                assert not future.done()
                raise asyncio.CancelledError

            agent._conn.prompt = _fake_prompt
            agent._conn.cancel = _fake_cancel
            agent._session_id = "test-session"

            with patch.object(
                ACPAgent,
                "_drain_cancelled_prompt",
                new=_raise_during_drain,
            ):
                task = asyncio.create_task(
                    agent.astep(conversation, on_event=lambda _: None)
                )
                await asyncio.wait_for(prompt_entered.wait(), timeout=5.0)
                task.cancel()
                try:
                    with pytest.raises(asyncio.CancelledError):
                        await task
                finally:
                    prompt_released.set()

        try:
            agent._executor = executor
            asyncio.run(_run_with_double_cancel())
        finally:
            executor.close()

        assert agent._restart_session_on_next_turn is True

    def test_astep_double_cancel_during_cancel_send_restarts_next_turn(self, tmp_path):
        """A second cancellation during session/cancel should quarantine prompt."""
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        executor = AsyncExecutor()

        async def _run_with_cancelled_cancel_send() -> None:
            prompt_entered = asyncio.Event()
            prompt_released = threading.Event()
            caller_loop = asyncio.get_running_loop()

            async def _fake_prompt(prompt_blocks, session_id):  # noqa: ARG001
                caller_loop.call_soon_threadsafe(prompt_entered.set)
                released = await asyncio.to_thread(prompt_released.wait, 10.0)
                assert released
                return None

            async def _raise_during_cancel_send(self):  # noqa: ARG001
                raise asyncio.CancelledError

            agent._conn.prompt = _fake_prompt
            agent._session_id = "test-session"

            with patch.object(
                ACPAgent,
                "_arequest_session_cancel",
                new=_raise_during_cancel_send,
            ):
                task = asyncio.create_task(
                    agent.astep(conversation, on_event=lambda _: None)
                )
                await asyncio.wait_for(prompt_entered.wait(), timeout=5.0)
                task.cancel()
                try:
                    with pytest.raises(asyncio.CancelledError):
                        await task
                finally:
                    prompt_released.set()

        try:
            agent._executor = executor
            asyncio.run(_run_with_cancelled_cancel_send())
        finally:
            executor.close()

        assert agent._restart_session_on_next_turn is True

    def test_cleanup_interruption_finalizes_completed_prompt(self, tmp_path):
        """A completed prompt should be finalized if cleanup is cancelled."""
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._session_id = "test-session"

        prompt_future: Future[PromptResponse | None] = Future()
        prompt_future.set_result(None)
        emitted = []

        with conversation.state as state:
            agent._handle_cancelled_cleanup_interruption(
                prompt_future,
                0.1,
                state,
                emitted.append,
            )

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        assert agent._restart_session_on_next_turn is False
        assert any(isinstance(event, ActionEvent) for event in emitted)

    def test_astep_cancellation_does_not_mark_suffix_installed(self, tmp_path):
        """Cancellation before a turn completes must leave
        ``_suffix_install_state`` as ``pending_first_prompt``.

        Otherwise the local state would say "installed" while the ACP
        server never received the suffix (the cancel landed before the
        portal task could persist it), and the next turn would skip
        re-injection.  Mirrors the ``_build_acp_prompt`` contract that
        the install state is only committed via
        ``_finalize_successful_turn`` → ``_commit_suffix_installation``.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent(
            agent_context=AgentContext(
                system_message_suffix="Team rules.", current_datetime=None
            )
        )
        conversation = self._make_conversation_with_message(tmp_path)
        agent._installed_suffix = agent.agent_context.to_acp_prompt_context()  # type: ignore[union-attr]
        agent._suffix_install_state = "pending_first_prompt"

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        executor = AsyncExecutor()

        async def _run_with_cancel() -> None:
            prompt_entered = asyncio.Event()
            prompt_released = threading.Event()
            caller_loop = asyncio.get_running_loop()

            async def _fake_prompt(prompt_blocks, session_id):
                caller_loop.call_soon_threadsafe(prompt_entered.set)
                released = await asyncio.to_thread(prompt_released.wait, 10.0)
                assert released
                return None

            async def _fake_cancel(session_id):
                assert session_id == "test-session"

            agent._conn.prompt = _fake_prompt
            agent._conn.cancel = _fake_cancel
            agent._session_id = "test-session"

            task = asyncio.create_task(
                agent.astep(conversation, on_event=lambda _: None)
            )
            await asyncio.wait_for(prompt_entered.wait(), timeout=5.0)
            task.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    with patch(
                        "openhands.sdk.agent.acp_agent._ACP_CANCEL_DRAIN_TIMEOUT",
                        0.01,
                    ):
                        await task
            finally:
                prompt_released.set()

        try:
            agent._executor = executor
            asyncio.run(_run_with_cancel())
        finally:
            executor.close()

        # Cancellation hit before _finalize_successful_turn ran, so the
        # suffix install state must remain pending — a subsequent turn
        # will re-inject the suffix.
        assert agent._suffix_install_state == "pending_first_prompt", (
            f"suffix install state was prematurely flipped to "
            f"{agent._suffix_install_state!r} — next turn would skip suffix"
        )
        # ``acp_suffix_installed`` must also not be persisted into
        # ``agent_state``: otherwise a process restart between this
        # cancelled turn and the next would read the marker and skip
        # re-injection (issue #3359 review thread 7).
        assert conversation.state.agent_state.get("acp_suffix_installed") is not True, (
            "acp_suffix_installed was persisted despite cancellation — "
            "a process restart would skip suffix re-injection"
        )

    def test_astep_does_not_deadlock_under_reentrant_state_lock(self, tmp_path):
        """End-to-end shape of the #3348 bug.

        Covers direct callers that hold ``state.lock`` on the loop thread
        across ``await astep(...)`` while a post-prompt callback
        re-acquires it. With astep overridden, the callback runs on the
        same thread as the lock owner — FIFOLock's reentrancy lets it
        through. Without the override, this hangs.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        state = conversation.state

        mock_client = _OpenHandsACPBridge()
        mock_client.get_turn_usage_update = MagicMock(return_value=object())
        agent._client = mock_client
        agent._conn = MagicMock()

        async def _fake_prompt(prompt_blocks, session_id):
            mock_client.accumulated_text.append("done")
            return None

        agent._conn.prompt = _fake_prompt
        agent._session_id = "test-session"

        executor = AsyncExecutor()
        try:
            agent._executor = executor

            # stats_callback-shaped re-entry: take the state lock briefly
            # from each event callback.  Same-thread reentry must succeed.
            def _capture_event(event):
                with state:
                    pass

            async def _arun_shaped() -> None:
                with state:
                    await asyncio.wait_for(
                        agent.astep(conversation, on_event=_capture_event),
                        timeout=10.0,
                    )

            asyncio.run(_arun_shaped())
        finally:
            executor.close()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )


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
        # A bearer-secret-looking id so the log-hygiene assertion below is
        # meaningful: the timeout warning must fingerprint it to ``...<last-8>``,
        # never emit the full id.
        agent._session_id = "sk-resume-secret-DEADBEEF"

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
        # Bearer session id is fingerprinted, not leaked, in the timeout warning.
        assert "sk-resume-secret-DEADBEEF" not in caplog.text
        assert "...DEADBEEF" in caplog.text
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

        # Should be: 2 tool call events (live) + finish action + finish observation
        assert len(events) == 4
        assert isinstance(events[0], ACPToolCallEvent)
        assert isinstance(events[1], ACPToolCallEvent)
        assert isinstance(events[2], ActionEvent)

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

        # ActionEvent(FinishAction) + ObservationEvent(FinishObservation)
        assert len(events) == 2
        assert isinstance(events[0], ActionEvent)

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

        # Only the FinishAction + FinishObservation should appear —
        # the old tool call was cleared by reset()
        assert len(events) == 2
        assert isinstance(events[0], ActionEvent)


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
# acp_session_mode field
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _select_auth_method
# ---------------------------------------------------------------------------


_CHATGPT_AUTH_JSON = '{"tokens": {"id_token": "x", "access_token": "y"}}'


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
        (auth_dir / "auth.json").write_text(_CHATGPT_AUTH_JSON, encoding="utf-8")

        env = {"OPENAI_API_KEY": "sk-test"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "chatgpt"

    def test_api_key_fallback_when_no_chatgpt_file(self, tmp_path):
        """Falls back to API key when chatgpt is offered but auth file absent."""
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"OPENAI_API_KEY": "sk-test"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "openai-api-key"

    def test_no_matching_credentials(self, tmp_path):
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"UNRELATED": "value"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) is None

    def test_chatgpt_auth_file(self, tmp_path):
        methods = [self._make_auth_method("chatgpt")]
        auth_dir = tmp_path / ".codex"
        auth_dir.mkdir()
        (auth_dir / "auth.json").write_text(_CHATGPT_AUTH_JSON, encoding="utf-8")

        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, {}) == "chatgpt"

    def test_gemini_oauth_personal_when_creds_file_present(self, tmp_path):
        """gemini-cli's OAuth login is selected when ~/.gemini/oauth_creds.json
        exists, with no GEMINI_API_KEY needed."""
        methods = [
            self._make_auth_method("oauth-personal"),
            self._make_auth_method("gemini-api-key"),
        ]
        gem_dir = tmp_path / ".gemini"
        gem_dir.mkdir()
        (gem_dir / "oauth_creds.json").write_text("{}", encoding="utf-8")

        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, {}) == "oauth-personal"

    def test_gemini_oauth_preferred_over_api_key(self, tmp_path):
        """OAuth login takes precedence over GEMINI_API_KEY (mirrors chatgpt)."""
        methods = [
            self._make_auth_method("oauth-personal"),
            self._make_auth_method("gemini-api-key"),
        ]
        gem_dir = tmp_path / ".gemini"
        gem_dir.mkdir()
        (gem_dir / "oauth_creds.json").write_text("{}", encoding="utf-8")

        env = {"GEMINI_API_KEY": "g-test"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "oauth-personal"

    def test_gemini_api_key_fallback_when_no_oauth_file(self, tmp_path):
        """Falls back to GEMINI_API_KEY when oauth-personal is offered but the
        creds file is absent (e.g. in a server image)."""
        methods = [
            self._make_auth_method("oauth-personal"),
            self._make_auth_method("gemini-api-key"),
        ]
        env = {"GEMINI_API_KEY": "g-test"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "gemini-api-key"

    def test_gemini_oauth_offered_but_no_creds_no_key(self, tmp_path):
        """oauth-personal offered, no creds file and no API key -> None."""
        methods = [
            self._make_auth_method("oauth-personal"),
            self._make_auth_method("gemini-api-key"),
        ]
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, {}) is None

    def test_empty_auth_methods(self):
        assert _select_auth_method([], {}) is None

    def test_method_not_in_server_list(self, tmp_path):
        """Even if env var is set, method must be offered by server."""
        methods = [self._make_auth_method("chatgpt")]
        env = {"OPENAI_API_KEY": "sk-test"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) is None

    # -- CODEX_HOME-aware chatgpt detection (issue #1020) ------------------

    def test_chatgpt_detected_under_relocated_codex_home(self, tmp_path):
        """chatgpt is selected when auth.json lives under a relocated CODEX_HOME,
        even though ~/.codex/auth.json does not exist."""
        codex_home = tmp_path / "conv" / "acp" / "codex"
        codex_home.mkdir(parents=True)
        (codex_home / "auth.json").write_text(_CHATGPT_AUTH_JSON, encoding="utf-8")
        methods = [self._make_auth_method("chatgpt")]
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=empty_home):
            assert (
                _select_auth_method(methods, {"CODEX_HOME": str(codex_home)})
                == "chatgpt"
            )

    def test_codex_home_without_auth_file_falls_back(self, tmp_path):
        """A CODEX_HOME without auth.json is not detected as chatgpt; it falls
        back to the API key when offered."""
        codex_home = tmp_path / "empty_codex_home"
        codex_home.mkdir()
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"CODEX_HOME": str(codex_home), "OPENAI_API_KEY": "sk-test"}
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=empty_home):
            assert _select_auth_method(methods, env) == "openai-api-key"

    def test_codex_auth_file_honors_codex_home(self, tmp_path):
        """_codex_auth_file points at $CODEX_HOME/auth.json when set, else
        ~/.codex/auth.json."""
        home = tmp_path / "home"
        home.mkdir()
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=home):
            assert _codex_auth_file({}) == home / ".codex" / "auth.json"
        ch = tmp_path / "ch"
        assert _codex_auth_file({"CODEX_HOME": str(ch)}) == ch / "auth.json"

    # -- apikey-format auth.json must not be treated as chatgpt (#3627) -----

    def test_apikey_format_auth_file_falls_back_to_api_key(self, tmp_path):
        """Codex rewrites $CODEX_HOME/auth.json with {"auth_mode": "apikey", ...}
        during apikey-mode sessions. On a restart, that file must NOT be picked
        as ``chatgpt`` or codex-acp hangs on browser-based OAuth (issue #3627).
        """
        codex_home = tmp_path / "codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(
            '{"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test"}',
            encoding="utf-8",
        )
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"CODEX_HOME": str(codex_home), "OPENAI_API_KEY": "sk-test"}
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=empty_home):
            assert _select_auth_method(methods, env) == "openai-api-key"

    def test_malformed_auth_file_falls_back_to_api_key(self, tmp_path):
        """A non-JSON / unreadable auth.json must not trip chatgpt selection."""
        codex_home = tmp_path / "codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text("not-json{", encoding="utf-8")
        methods = [
            self._make_auth_method("chatgpt"),
            self._make_auth_method("openai-api-key"),
        ]
        env = {"CODEX_HOME": str(codex_home), "OPENAI_API_KEY": "sk-test"}
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=empty_home):
            assert _select_auth_method(methods, env) == "openai-api-key"

    # -- Gemini Vertex AI service-account detection (issue #1020) ----------

    def test_vertex_ai_selected_when_credentials_file_present(self, tmp_path):
        """vertex-ai is selected when GOOGLE_APPLICATION_CREDENTIALS points at an
        existing service-account JSON."""
        sa = tmp_path / "gcloud-credentials.json"
        sa.write_text("{}", encoding="utf-8")
        methods = [
            self._make_auth_method("vertex-ai"),
            self._make_auth_method("oauth-personal"),
            self._make_auth_method("gemini-api-key"),
        ]
        env = {"GOOGLE_APPLICATION_CREDENTIALS": str(sa), "GEMINI_API_KEY": "g"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "vertex-ai"

    def test_vertex_ai_preferred_over_personal_oauth(self, tmp_path):
        """The materialised Vertex SA (deployable) wins over a host-bound
        personal OAuth login file."""
        sa = tmp_path / "sa.json"
        sa.write_text("{}", encoding="utf-8")
        gem_dir = tmp_path / ".gemini"
        gem_dir.mkdir()
        (gem_dir / "oauth_creds.json").write_text("{}", encoding="utf-8")
        methods = [
            self._make_auth_method("vertex-ai"),
            self._make_auth_method("oauth-personal"),
        ]
        env = {"GOOGLE_APPLICATION_CREDENTIALS": str(sa)}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "vertex-ai"

    def test_vertex_ai_offered_but_no_credentials_file(self, tmp_path):
        """vertex-ai offered but GOOGLE_APPLICATION_CREDENTIALS missing/empty ->
        falls through to the API-key fallback."""
        methods = [
            self._make_auth_method("vertex-ai"),
            self._make_auth_method("gemini-api-key"),
        ]
        env = {"GEMINI_API_KEY": "g"}
        with patch("openhands.sdk.agent.acp_agent.Path.home", return_value=tmp_path):
            assert _select_auth_method(methods, env) == "gemini-api-key"


# ---------------------------------------------------------------------------
# _codex_base_url_overrides (codex ignores OPENAI_BASE_URL)
# ---------------------------------------------------------------------------


class TestCodexBaseUrlOverrides:
    def test_pins_base_url_for_codex(self):
        # The documented one-liner: override the built-in openai provider's URL.
        ov = _codex_base_url_overrides(
            "codex-acp", [], {"OPENAI_BASE_URL": "https://proxy.example"}
        )
        assert ov == ["-c", 'openai_base_url="https://proxy.example"']

    def test_detects_codex_in_any_token(self):
        # e.g. launched via npx with the scoped package name
        ov = _codex_base_url_overrides(
            "npx",
            ["-y", "@zed-industries/codex-acp@0.15.0"],
            {"OPENAI_BASE_URL": "https://p"},
        )
        assert ov == ["-c", 'openai_base_url="https://p"']

    def test_noop_for_non_codex(self):
        assert (
            _codex_base_url_overrides(
                "claude-agent-acp", [], {"OPENAI_BASE_URL": "https://p"}
            )
            == []
        )

    def test_noop_when_no_base_url(self):
        assert _codex_base_url_overrides("codex-acp", [], {}) == []

    def test_noop_when_caller_already_set_base_url(self):
        args = ["-c", 'openai_base_url="https://other"']
        assert (
            _codex_base_url_overrides(
                "codex-acp", args, {"OPENAI_BASE_URL": "https://p"}
            )
            == []
        )

    def test_noop_when_caller_already_set_provider(self):
        args = ["-c", 'model_provider="custom"']
        assert (
            _codex_base_url_overrides(
                "codex-acp", args, {"OPENAI_BASE_URL": "https://p"}
            )
            == []
        )


class TestCodexModelConfigOptions:
    def test_splits_combined_model_and_reasoning_effort(self):
        assert _codex_model_config_options("gpt-5.5/high") == (
            ("model", "gpt-5.5"),
            ("reasoning_effort", "high"),
        )

    def test_leaves_base_or_custom_model_id_unchanged(self):
        assert _codex_model_config_options("gpt-5.5") == (("model", "gpt-5.5"),)
        assert _codex_model_config_options("custom/provider/model") == (
            ("model", "custom/provider/model"),
        )


# ---------------------------------------------------------------------------
# ACP model overrides
# ---------------------------------------------------------------------------


class TestMaybeSetSessionModel:
    @pytest.mark.asyncio
    async def test_set_session_model_mechanism(self):
        # ``via_config_option=False`` (gemini-cli, older codex/claude with the
        # ``models`` capability) applies the model via ``set_session_model``.
        conn = AsyncMock()
        applied = await _maybe_set_session_model(
            conn, "codex-acp", "session-1", "gpt-5.4", via_config_option=False
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="gpt-5.4",
            session_id="session-1",
        )
        conn.set_config_option.assert_not_called()
        # The override was actually pushed to the server via the protocol call.
        assert applied is True

    @pytest.mark.asyncio
    async def test_set_config_option_mechanism(self):
        # ``via_config_option=True`` (codex-acp 0.16+, claude-agent-acp 0.44+)
        # applies the model via ``set_config_option(configId="model")``.
        conn = AsyncMock()
        applied = await _maybe_set_session_model(
            conn,
            "claude-agent-acp",
            "session-1",
            "opus[1m]",
            via_config_option=True,
        )
        conn.set_config_option.assert_awaited_once_with(
            config_id="model",
            value="opus[1m]",
            session_id="session-1",
        )
        conn.set_session_model.assert_not_called()
        assert applied is True

    @pytest.mark.asyncio
    async def test_codex_config_option_splits_reasoning_effort(self):
        # Canvas may persist Codex ids as ``model/effort``; codex-acp 0.16
        # exposes effort as its own config option.
        conn = AsyncMock()
        applied = await _maybe_set_session_model(
            conn, "codex-acp", "session-1", "gpt-5.4/low", via_config_option=True
        )
        conn.set_session_model.assert_not_called()
        conn.set_config_option.assert_has_awaits(
            [
                call(config_id="model", value="gpt-5.4", session_id="session-1"),
                call(
                    config_id="reasoning_effort",
                    value="low",
                    session_id="session-1",
                ),
            ]
        )
        assert conn.set_config_option.await_count == 2
        assert applied is True

    @pytest.mark.asyncio
    async def test_missing_model_skips_protocol_override(self):
        conn = AsyncMock()
        applied = await _maybe_set_session_model(
            conn, "codex-acp", "session-1", None, via_config_option=True
        )
        conn.set_session_model.assert_not_called()
        conn.set_config_option.assert_not_called()
        assert applied is False

    @pytest.mark.asyncio
    async def test_unknown_provider_applies_via_detected_mechanism(self):
        # An unknown/custom server applies the model via whichever mechanism its
        # session/new advertised (detected upstream), not a hardcoded guess.
        conn_cfg = AsyncMock()
        applied = await _maybe_set_session_model(
            conn_cfg, "devin-cli", "session-1", "kimi-k2-6", via_config_option=True
        )
        conn_cfg.set_config_option.assert_awaited_once_with(
            config_id="model", value="kimi-k2-6", session_id="session-1"
        )
        conn_cfg.set_session_model.assert_not_called()
        assert applied is True

        conn_set = AsyncMock()
        applied = await _maybe_set_session_model(
            conn_set, "devin-cli", "session-1", "kimi-k2-6", via_config_option=False
        )
        conn_set.set_session_model.assert_awaited_once_with(
            model_id="kimi-k2-6", session_id="session-1"
        )
        conn_set.set_config_option.assert_not_called()
        assert applied is True

    @pytest.mark.asyncio
    async def test_unknown_provider_apply_failure_is_tolerated(self):
        # If the model-apply fails for an unknown provider, we log a warning but
        # don't break session creation.
        conn = AsyncMock()
        conn.set_config_option.side_effect = ACPRequestError(
            code=-32601, message="method not found"
        )
        applied = await _maybe_set_session_model(
            conn, "some-custom-acp", "session-1", "whatever", via_config_option=True
        )
        conn.set_config_option.assert_awaited_once()
        assert applied is False

    @pytest.mark.asyncio
    async def test_known_provider_apply_rejection_is_tolerated(self):
        # claude-agent-acp's ``model`` select is dynamic/account-dependent and
        # rejects an absent curated id (e.g. ``sonnet`` on accounts without it,
        # dressed as -32603). A rejected *initial* model must not fail session
        # creation — it degrades to the server default (applied=False) like the
        # unknown-provider path, since the curated list is a suggestion not an
        # access check. (Runtime switches via set_acp_model surface the error.)
        conn = AsyncMock()
        conn.set_config_option.side_effect = ACPRequestError(
            code=-32603, message="Invalid value for config option model: sonnet"
        )
        applied = await _maybe_set_session_model(
            conn, "claude-agent-acp", "session-1", "sonnet", via_config_option=True
        )
        conn.set_config_option.assert_awaited_once()
        conn.set_session_model.assert_not_called()
        assert applied is False


class TestReapplySessionModelOnResume:
    """Resume reapplies the persisted model via the runtime-switch gate."""

    @pytest.mark.asyncio
    async def test_reapply_via_set_session_model_mechanism(self):
        # load_session() carries no model selection, so on resume the persisted
        # model must be reapplied via the runtime-switch gate. Servers with the
        # ``models`` capability (gemini-cli, older codex/claude) use
        # ``set_session_model``.
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn,
            "claude-agent-acp",
            "sess-1",
            "claude-haiku-4-5-20251001",
            via_config_option=False,
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="claude-haiku-4-5-20251001", session_id="sess-1"
        )
        assert applied is True

    @pytest.mark.asyncio
    async def test_reapply_via_set_config_option_mechanism(self):
        # codex-acp 0.16+/claude-agent-acp 0.44+ reapply via
        # ``set_config_option(configId="model")`` with the bare preset id.
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "codex-acp", "sess-1", "gpt-5.5", via_config_option=True
        )
        conn.set_config_option.assert_awaited_once_with(
            config_id="model", value="gpt-5.5", session_id="sess-1"
        )
        conn.set_session_model.assert_not_called()
        assert applied is True

    @pytest.mark.asyncio
    async def test_codex_reapply_splits_reasoning_effort(self):
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "codex-acp", "sess-1", "gpt-5.4/low", via_config_option=True
        )
        conn.set_session_model.assert_not_called()
        conn.set_config_option.assert_has_awaits(
            [
                call(config_id="model", value="gpt-5.4", session_id="sess-1"),
                call(
                    config_id="reasoning_effort",
                    value="low",
                    session_id="sess-1",
                ),
            ]
        )
        assert conn.set_config_option.await_count == 2
        assert applied is True

    @pytest.mark.asyncio
    async def test_missing_model_skips_reapply(self):
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "claude-agent-acp", "sess-1", None, via_config_option=True
        )
        conn.set_session_model.assert_not_called()
        conn.set_config_option.assert_not_called()
        assert applied is False

    @pytest.mark.asyncio
    async def test_unknown_provider_reapplies_via_detected_mechanism(self):
        # provider=None (custom server) reapplies via whichever mechanism the
        # resumed session advertised, not a hardcoded guess.
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "devin-cli", "sess-1", "kimi-k2-6", via_config_option=True
        )
        conn.set_session_model.assert_not_called()
        conn.set_config_option.assert_awaited_once_with(
            config_id="model",
            value="kimi-k2-6",
            session_id="sess-1",
        )
        assert applied is True

    @pytest.mark.asyncio
    async def test_known_unsupported_provider_skips_reapply(self):
        from openhands.sdk.settings.acp_providers import ACPProviderInfo

        unsupported = ACPProviderInfo(
            key="legacy",
            display_name="Legacy",
            default_command=("legacy",),
            api_key_env_var=None,
            base_url_env_var=None,
            default_session_mode="default",
            agent_name_patterns=("legacy",),
            supports_set_session_model=False,
            supports_runtime_model_switch=False,
            session_meta_key=None,
        )
        conn = AsyncMock()
        with patch(
            "openhands.sdk.agent.acp_agent.detect_acp_provider_by_agent_name",
            return_value=unsupported,
        ):
            applied = await _reapply_session_model_on_resume(
                conn, "legacy-acp", "sess-1", "x", via_config_option=False
            )
        conn.set_session_model.assert_not_called()
        assert applied is False

    @pytest.mark.asyncio
    async def test_client_rejection_is_swallowed_on_resume(self):
        # A client/protocol rejection (method-not-found = server doesn't support
        # the call, or invalid model id) must not break resume — mirrors the
        # load_session fallback. The error is logged, not raised.
        conn = AsyncMock()
        conn.set_config_option.side_effect = ACPRequestError(
            code=-32601, message="method not found"
        )
        applied = await _reapply_session_model_on_resume(
            conn, "some-custom-acp", "sess-1", "whatever", via_config_option=True
        )
        conn.set_config_option.assert_awaited_once()
        # Rejected => the live session kept the server default, so the override
        # must NOT be reported as applied.
        assert applied is False

    @pytest.mark.asyncio
    async def test_any_request_error_is_swallowed_on_resume(self):
        # Any ACPRequestError (here a -32603 server error) is tolerated on
        # resume — like the load_session fallback — so a flaky/stale server
        # can't break session startup; the session keeps the server default.
        conn = AsyncMock()
        conn.set_session_model.side_effect = ACPRequestError(
            code=-32603, message="internal error"
        )
        applied = await _reapply_session_model_on_resume(
            conn, "codex-acp", "sess-1", "gpt-5.5", via_config_option=False
        )
        conn.set_session_model.assert_awaited_once()
        assert applied is False


class TestHasLiveACPSession:
    """The public predicate LocalConversation branches on for model switching."""

    def test_false_before_session(self):
        # Freshly constructed: no connection / session / executor wired yet.
        agent = _make_agent()
        assert agent.has_live_acp_session is False

    def test_true_when_fully_wired(self):
        agent = _make_agent()
        agent._conn = MagicMock()
        agent._session_id = "sess-1"
        agent._executor = MagicMock()
        assert agent.has_live_acp_session is True

    @pytest.mark.parametrize(
        "conn,session_id,executor",
        [
            (None, "sess-1", "exec"),
            ("conn", None, "exec"),
            ("conn", "sess-1", None),
        ],
    )
    def test_false_when_partially_wired(self, conn, session_id, executor):
        # Every one of the three runtime handles must be present; a partial
        # teardown / bootstrap must not read as a live session.
        agent = _make_agent()
        agent._conn = MagicMock() if conn else None
        agent._session_id = session_id
        agent._executor = MagicMock() if executor else None
        assert agent.has_live_acp_session is False

    def test_set_acp_model_raises_when_no_live_session(self):
        # The low-level primitive still rejects a pre-session call (the deferral
        # lives in LocalConversation.switch_acp_model, which branches on the
        # predicate); guards that the two stay in sync.
        agent = _make_agent()
        assert not agent.has_live_acp_session
        with pytest.raises(RuntimeError, match="not initialized"):
            agent.set_acp_model("gpt-5.4")


class TestSetACPModel:
    """Runtime (mid-conversation) model switching via the session's mechanism."""

    @staticmethod
    def _wire(
        agent: ACPAgent, agent_name: str, *, via_config_option: bool = False
    ) -> ACPAgent:
        conn = MagicMock()
        conn.set_session_model = AsyncMock()
        conn.set_config_option = AsyncMock()
        agent._conn = conn
        agent._session_id = "sess-1"
        agent._agent_name = agent_name
        agent._model_via_config_option = via_config_option
        executor = MagicMock()

        # run_async actually drives the (async) apply coroutine to completion so
        # the AsyncMock conn calls fire — exercising the real apply/split path.
        def _run(coro: Any, timeout: Any = None) -> Any:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        executor.run_async = MagicMock(side_effect=_run)
        agent._executor = executor
        return agent

    def test_switches_model_on_live_codex_session(self):
        # Default (models-capability) session ⇒ set_session_model, id as-is.
        agent = self._wire(_make_agent(), "codex-acp")
        agent.set_acp_model("gpt-5.5")
        agent._conn.set_session_model.assert_awaited_once_with(
            model_id="gpt-5.5", session_id="sess-1"
        )
        agent._conn.set_config_option.assert_not_called()
        agent._executor.run_async.assert_called_once()
        # Sentinel LLM + metrics reflect the live model for cost/token tracking.
        assert agent.llm.model == "gpt-5.5"
        assert agent.llm.metrics.model_name == "gpt-5.5"

    def test_switches_codex_via_config_option_single_call(self):
        # codex-acp 0.16 configOptions: the bare preset id applies as a single
        # `model` selection.
        agent = self._wire(_make_agent(), "codex-acp", via_config_option=True)
        agent.set_acp_model("gpt-5.5")
        agent._conn.set_config_option.assert_awaited_once_with(
            config_id="model", value="gpt-5.5", session_id="sess-1"
        )
        agent._conn.set_session_model.assert_not_called()
        assert agent.llm.model == "gpt-5.5"
        assert agent._current_model_id == "gpt-5.5"

    def test_switches_codex_via_config_option_splits_reasoning_effort(self):
        agent = self._wire(_make_agent(), "codex-acp", via_config_option=True)
        agent.set_acp_model("gpt-5.5/high")
        agent._conn.set_config_option.assert_has_awaits(
            [
                call(config_id="model", value="gpt-5.5", session_id="sess-1"),
                call(
                    config_id="reasoning_effort",
                    value="high",
                    session_id="sess-1",
                ),
            ]
        )
        assert agent._conn.set_config_option.await_count == 2
        agent._conn.set_session_model.assert_not_called()
        assert agent.llm.model == "gpt-5.5/high"
        assert agent._current_model_id == "gpt-5.5/high"

    def test_switches_claude_via_config_option_single_call(self):
        # A bare id (no `/`) applies as a single `model` selection — no effort.
        agent = self._wire(_make_agent(), "claude-agent-acp", via_config_option=True)
        agent.set_acp_model("sonnet")
        agent._conn.set_config_option.assert_awaited_once_with(
            config_id="model", value="sonnet", session_id="sess-1"
        )
        assert agent._current_model_id == "sonnet"

    def test_switch_method_not_found_raises_no_fallback(self):
        # No cross-mechanism fallback: a -32601 surfaces as a ValueError naming
        # the advertised mechanism, and the other call is never attempted.
        agent = self._wire(_make_agent(), "codex-acp", via_config_option=False)
        agent._conn.set_session_model.side_effect = ACPRequestError(
            code=-32601, message="Method not found"
        )
        with pytest.raises(ValueError, match="rejected set_session_model"):
            agent.set_acp_model("gpt-5.5")
        agent._conn.set_config_option.assert_not_called()
        agent._executor.run_async.assert_called_once()
        # Mechanism + sentinel model are left unchanged on a failed switch.
        assert agent._model_via_config_option is False
        assert agent.llm.model != "gpt-5.5"

    def test_switch_invalid_model_raises_value_error(self):
        # A -32602 invalid-params is a real client error, not a wrong-mechanism
        # signal: surface it as ValueError without a second call.
        agent = self._wire(_make_agent(), "codex-acp", via_config_option=True)
        agent._conn.set_config_option.side_effect = ACPRequestError(
            code=-32602, message="Invalid params"
        )
        with pytest.raises(ValueError, match="rejected set_config_option"):
            agent.set_acp_model("nonsense")
        agent._executor.run_async.assert_called_once()

    def test_claude_provider_supports_runtime_switch(self):
        agent = self._wire(_make_agent(), "claude-agent-acp")
        agent.set_acp_model("claude-haiku-4-5-20251001")
        agent._conn.set_session_model.assert_called_once_with(
            model_id="claude-haiku-4-5-20251001", session_id="sess-1"
        )

    def test_unknown_provider_still_attempts_switch(self):
        # A custom/unrecognised server (provider=None) is allowed to attempt
        # the call; the ACP layer errors if it isn't actually supported.
        agent = self._wire(_make_agent(), "some-custom-acp")
        agent.set_acp_model("whatever")
        agent._conn.set_session_model.assert_called_once()

    def test_rejects_empty_model(self):
        agent = self._wire(_make_agent(), "codex-acp")
        with pytest.raises(ValueError, match="non-empty"):
            agent.set_acp_model("   ")
        agent._conn.set_session_model.assert_not_called()

    def test_raises_before_session_initialized(self):
        agent = _make_agent()  # no _conn / _session_id / _executor
        with pytest.raises(RuntimeError, match="not initialized"):
            agent.set_acp_model("gpt-5.4")

    def test_raises_for_provider_without_protocol_support(self):
        from openhands.sdk.settings.acp_providers import ACPProviderInfo

        unsupported = ACPProviderInfo(
            key="legacy",
            display_name="Legacy",
            default_command=("legacy",),
            api_key_env_var=None,
            base_url_env_var=None,
            default_session_mode="default",
            agent_name_patterns=("legacy",),
            supports_set_session_model=False,
            supports_runtime_model_switch=False,
            session_meta_key=None,
        )
        agent = self._wire(_make_agent(), "legacy-acp")
        with patch(
            "openhands.sdk.agent.acp_agent.detect_acp_provider_by_agent_name",
            return_value=unsupported,
        ):
            with pytest.raises(ValueError, match="does not support runtime"):
                agent.set_acp_model("x")
        agent._conn.set_session_model.assert_not_called()

    def test_translates_acp_request_error_to_value_error(self):
        # A protocol-level rejection (e.g. method-not-found on a custom server,
        # or an invalid model id) must surface as a ValueError — not leak as a
        # raw acp.exceptions.RequestError — so the agent-server maps it to 400.
        # The rejection is raised by the conn (driven through the real apply
        # coroutine), exercising set_acp_model's actual error path.
        agent = self._wire(_make_agent(), "codex-acp")
        agent._conn.set_session_model.side_effect = ACPRequestError(
            code=-32601, message="method not found"
        )
        with pytest.raises(ValueError, match="rejected set_session_model"):
            agent.set_acp_model("bogus-model")
        # The sentinel LLM must not be mutated when the switch fails.
        assert agent.llm.model != "bogus-model"

    def test_propagates_server_internal_error(self):
        # JSON-RPC -32603 is a server-internal failure, not a bad client
        # request. It must propagate (as the raw ACPRequestError -> 5xx) rather
        # than be mislabeled as a 400-class ValueError, mirroring the retriable
        # handling on the prompt path.
        agent = self._wire(_make_agent(), "codex-acp")
        agent._conn.set_session_model.side_effect = ACPRequestError(
            code=-32603, message="internal error"
        )
        with pytest.raises(ACPRequestError):
            agent.set_acp_model("some-model")
        # The sentinel LLM must not be mutated when the switch fails.
        assert agent.llm.model != "some-model"

    def test_passes_timeout_to_run_async(self):
        # The protocol round-trip runs under the conversation state lock, so it
        # must be bounded to avoid wedging the lock if the server never answers.
        agent = self._wire(_make_agent(acp_prompt_timeout=42.0), "codex-acp")
        agent.set_acp_model("gpt-5.5")
        _, kwargs = agent._executor.run_async.call_args
        assert kwargs["timeout"] == 42.0


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
        assert len(events) == 2
        assert isinstance(events[0], ActionEvent)
        assert isinstance(events[0].action, FinishAction)
        assert "Success after retry" in events[0].action.message

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
        assert isinstance(events[0], ActionEvent)
        assert isinstance(events[0].action, FinishAction)
        assert "Success after server error retry" in events[0].action.message

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


class TestGeminiSessionModel:
    @pytest.mark.asyncio
    async def test_gemini_cli_uses_protocol_model_override(self):
        # gemini-cli keeps the ``models`` capability, so model selection rides
        # ``set_session_model`` (via_config_option=False).
        conn = AsyncMock()
        await _maybe_set_session_model(
            conn, "gemini-cli", "session-1", "gemini-3-flash", via_config_option=False
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

    def test_cancel_drain_restart_keeps_retry_flag_when_init_fails(self, tmp_path):
        """A failed replacement session should leave the deferred restart armed."""
        agent = _make_agent()
        state = _make_state(tmp_path)
        agent._restart_session_on_next_turn = True

        with patch.object(ACPAgent, "init_state", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                agent._restart_session_after_drain_timeout(
                    state, on_event=lambda _: None
                )

        assert agent._restart_session_on_next_turn is True

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

    def test_cancel_drain_restart_preserves_session_id_for_resume(self, tmp_path):
        """A cancelled-prompt drain timeout restarts the subprocess, but should
        still load the persisted ACP session so the server keeps conversation
        memory.
        """
        agent = _make_agent(
            agent_context=AgentContext(system_message_suffix="Team rules.")
        )
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "stored-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_suffix_installed": True,
        }
        conn = self._make_conn()

        with self._transport_patches(conn):
            agent._restart_session_after_drain_timeout(state, on_event=lambda _: None)

        conn.load_session.assert_awaited_once()
        conn.new_session.assert_not_awaited()
        assert agent._session_id == "stored-sess"
        assert state.agent_state["acp_session_id"] == "stored-sess"
        assert state.agent_state["acp_session_cwd"] == str(tmp_path)
        assert state.agent_state["acp_suffix_installed"] is True
        assert agent._suffix_install_state == "installed"

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

    # ----- explicit acp_resume_session_id (the durable-mirror override) -----

    def test_acp_resume_session_id_drives_load_session_when_no_fs_state(self, tmp_path):
        """``acp_resume_session_id`` resumes even when ``agent_state`` is empty.

        Cloud sandboxes lose ``base_state.json`` on recycle, so the FS-persisted
        ``acp_session_id`` is gone.  The app-server mirrors the id into durable
        storage and passes it back via ``acp_resume_session_id`` — that should
        still drive ``load_session``.
        """
        agent = _make_agent(acp_resume_session_id="externally-stored-sess")
        state = _make_state(tmp_path)
        assert "acp_session_id" not in state.agent_state
        conn = self._make_conn()

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        _, kwargs = conn.load_session.call_args
        assert kwargs["session_id"] == "externally-stored-sess"
        assert kwargs["cwd"] == str(tmp_path)
        conn.new_session.assert_not_awaited()
        assert agent._session_id == "externally-stored-sess"

    def test_acp_resume_session_id_overrides_fs_session_id(self, tmp_path):
        """The explicit field wins over the FS-persisted id when they differ."""
        agent = _make_agent(acp_resume_session_id="durable-sess")
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "fs-sess",
            "acp_session_cwd": str(tmp_path),
        }
        conn = self._make_conn()

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        _, kwargs = conn.load_session.call_args
        assert kwargs["session_id"] == "durable-sess"
        conn.new_session.assert_not_awaited()
        assert agent._session_id == "durable-sess"

    def test_acp_resume_session_id_failure_falls_back_to_new_session(self, tmp_path):
        """If the server can't load the explicit id, fall back to new_session.

        The ACP server may have lost its own session storage (no PVC, different
        host …); failing closed by aborting is worse than starting fresh.
        Matches the existing ``load_session`` failure path.
        """
        agent = _make_agent(acp_resume_session_id="missing-sess")
        state = _make_state(tmp_path)
        conn = self._make_conn(
            new_session_id="replacement-sess",
            load_exc=ACPRequestError(-32602, "unknown session"),
        )

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        conn.new_session.assert_awaited_once()
        assert agent._session_id == "replacement-sess"

    def test_acp_resume_session_id_matches_fs_id_uses_fs_cwd(self, tmp_path):
        """When the explicit id equals the FS id, the FS cwd is reused.

        Avoids a spurious "infer cwd from current workspace" branch when the
        agent_state was just hydrated from the same id.
        """
        agent = _make_agent(acp_resume_session_id="same-sess")
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "same-sess",
            "acp_session_cwd": str(tmp_path),
        }
        conn = self._make_conn()

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        _, kwargs = conn.load_session.call_args
        assert kwargs["session_id"] == "same-sess"

    def test_session_ids_redacted_in_resume_log_lines(self, tmp_path, caplog):
        """Resume / fallback log lines must not emit plaintext session ids.

        ACP session ids are bearer tokens; log aggregators retain lines for
        weeks, so they're a serialization boundary in their own right. The
        ``_start_acp_server`` log lines must emit only a short suffix
        fingerprint, never the full id.
        """
        sensitive_explicit = "explicit-do-not-log-abc12345-LONGTAIL"
        sensitive_fs = "fs-session-do-not-log-OTHERTAIL"

        agent = _make_agent(acp_resume_session_id=sensitive_explicit)
        state = _make_state(tmp_path)
        state.agent_state = {**state.agent_state, "acp_session_id": sensitive_fs}
        conn = self._make_conn()
        with caplog.at_level("INFO"):
            self._patched_start_acp_server(agent, state, conn=conn)
        messages = "\n".join(rec.getMessage() for rec in caplog.records)
        assert sensitive_explicit not in messages
        assert sensitive_fs not in messages
        assert sensitive_explicit[-8:] in messages  # fingerprint suffix present

        caplog.clear()
        agent2 = _make_agent(acp_resume_session_id=sensitive_explicit)
        state2 = _make_state(tmp_path)
        conn2 = self._make_conn(
            new_session_id="replacement",
            load_exc=ACPRequestError(-32602, "unknown session"),
        )
        with caplog.at_level("WARNING"):
            self._patched_start_acp_server(agent2, state2, conn=conn2)
        fail_warnings = "\n".join(
            rec.getMessage()
            for rec in caplog.records
            if "load_session" in rec.getMessage()
        )
        assert sensitive_explicit not in fail_warnings

    def test_fingerprint_session_id_helper(self):
        """``_fingerprint_session_id`` returns a last-8 suffix, never the full id."""
        from openhands.sdk.agent.acp_agent import _fingerprint_session_id

        assert _fingerprint_session_id(None) == "<none>"
        assert _fingerprint_session_id("short") == "<short>"
        assert _fingerprint_session_id("exactly8") == "<short>"
        long_sid = "a" * 24 + "12345678"
        out = _fingerprint_session_id(long_sid)
        assert long_sid not in out
        assert out.endswith("12345678")
        assert out.startswith("...")

    # ----- acp_resume_session_id is a bearer secret on the wire -----

    def test_acp_resume_session_id_redacted_by_default(self):
        """Default serialization must mask ``acp_resume_session_id``."""
        sensitive = "super-secret-resume-id-do-not-leak"
        agent = _make_agent(acp_resume_session_id=sensitive)

        data_json = agent.model_dump_json()
        assert sensitive not in data_json, (
            f"plaintext id leaked into model_dump_json: {data_json}"
        )
        data = json.loads(data_json)
        assert data.get("acp_resume_session_id") == REDACTED_SECRET_VALUE

        py_dump = agent.model_dump()
        py_value = py_dump.get("acp_resume_session_id")
        assert sensitive not in repr(py_value)
        assert sensitive not in str(py_value)

    def test_acp_resume_session_id_none_serializes_as_none(self):
        """Absence is not a secret — ``None`` must round-trip as ``null``."""
        agent = _make_agent()
        data = json.loads(agent.model_dump_json())
        assert data.get("acp_resume_session_id") is None

    def test_acp_resume_session_id_redacted_sentinel_loads_as_none(self):
        """Default-redacted dump must reload as ``None``, not ``'**********'``.

        Without the matching validator, ``model_validate_json`` of a default
        dump would leave the field set to the literal sentinel — calling
        ``session/load`` with that fails server-side and we'd fall back to
        ``new_session`` every time, defeating the durable-mirror design.
        """
        sensitive = "super-secret-resume-id-do-not-leak"
        agent = _make_agent(acp_resume_session_id=sensitive)
        reloaded = ACPAgent.model_validate_json(agent.model_dump_json())
        assert reloaded.acp_resume_session_id is None

    def test_acp_resume_session_id_plaintext_roundtrip(self):
        """Plaintext dump (trusted backend) reloads verbatim without a cipher."""
        sensitive = "super-secret-resume-id-do-not-leak"
        agent = _make_agent(acp_resume_session_id=sensitive)
        exposed = agent.model_dump_json(context={"expose_secrets": "plaintext"})
        assert json.loads(exposed)["acp_resume_session_id"] == sensitive
        reloaded = ACPAgent.model_validate_json(exposed)
        assert reloaded.acp_resume_session_id == sensitive

    def test_acp_resume_session_id_encrypted_roundtrip(self):
        """Encrypted dump + cipher in context decrypts back to the real id."""
        sensitive = "super-secret-resume-id-do-not-leak"
        agent = _make_agent(acp_resume_session_id=sensitive)
        cipher = Cipher(secret_key="test-cipher-secret-key-for-roundtrip-only")

        encrypted_json = agent.model_dump_json(
            context={"expose_secrets": "encrypted", "cipher": cipher}
        )
        assert sensitive not in encrypted_json

        reloaded = ACPAgent.model_validate_json(
            encrypted_json, context={"cipher": cipher}
        )
        assert reloaded.acp_resume_session_id == sensitive

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

    def test_resume_preserves_persisted_model_when_load_session_omits_models(
        self, tmp_path
    ):
        """Resume must not blank the persisted ``acp_current_model_*`` when
        ``load_session`` returns no ``models`` field.

        The ``models`` capability is UNSTABLE; some agents only attach it to
        ``new_session`` responses, not ``load_session``. Previously
        ``init_state`` unconditionally overwrote ``agent_state`` with the
        freshly-extracted (possibly ``None``) values, dropping the chip on
        every resume. The contract is: only update model state when we
        actually learned something new.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "resumable-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_current_model_id": "claude-opus-4-1",
            "acp_available_models": [
                {
                    "model_id": "claude-opus-4-1",
                    "name": "Opus 4.1",
                    "description": None,
                }
            ],
        }
        # ``load_session`` returns a response whose ``models`` field is
        # absent — same shape as a server that doesn't surface the
        # UNSTABLE capability on resume responses.
        conn = self._make_conn()
        load_response = MagicMock(spec=[])  # spec=[] → no .models attribute
        conn.load_session = AsyncMock(return_value=load_response)

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        # Persisted values survive the resume even though load_session
        # didn't re-report them.
        assert state.agent_state["acp_current_model_id"] == "claude-opus-4-1"
        assert state.agent_state["acp_available_models"] == [
            {"model_id": "claude-opus-4-1", "name": "Opus 4.1", "description": None}
        ]

    def test_resume_with_forced_model_preserves_persisted_available_models(
        self, tmp_path
    ):
        """Resume with a switched ``acp_model`` must not blank the persisted
        ``acp_available_models``.

        Regression: ``current_model_id = self.acp_model or reported`` becomes
        non-null from the forced ``acp_model`` even when ``load_session`` omits
        the UNSTABLE ``models`` block (so ``_available_models`` is empty). The
        list persistence must be gated on actually receiving a list, not on
        ``current_model_id`` being set — otherwise the picker payload is wiped
        on every resume of a switched conversation.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        # A prior runtime switch made ``model-b`` the authoritative model.
        agent = _make_agent(acp_model="model-b")
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "resumable-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_current_model_id": "model-a",
            "acp_available_models": [
                {"model_id": "model-a", "name": "Model A", "description": None},
                {"model_id": "model-b", "name": "Model B", "description": None},
            ],
        }
        conn = self._make_conn()
        load_response = MagicMock(spec=[])  # no .models block
        conn.load_session = AsyncMock(return_value=load_response)

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        # current_model_id reflects the forced (switched) model...
        assert state.agent_state["acp_current_model_id"] == "model-b"
        # ...but the previously persisted list is preserved, not clobbered.
        assert state.agent_state["acp_available_models"] == [
            {"model_id": "model-a", "name": "Model A", "description": None},
            {"model_id": "model-b", "name": "Model B", "description": None},
        ]

    def test_resume_with_explicit_empty_models_clears_stale_list(self, tmp_path):
        """Resume where the server *explicitly* reports ``availableModels: []``
        must CLEAR the persisted list — not preserve it.

        Regression: a truthy ``if self._available_models`` check couldn't tell
        an omitted ``models`` block (preserve) from an explicit empty list
        (clear), so a server that dropped its models kept advertising stale
        picker options after resume. The ``None`` (absent) vs ``[]`` (reported
        empty) distinction from ``_extract_session_models`` fixes this.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "resumable-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_current_model_id": "model-a",
            "acp_available_models": [
                {"model_id": "model-a", "name": "Model A", "description": None},
            ],
        }
        # load_session DOES carry a ``models`` block, but the server now offers
        # no models (explicit empty list).
        conn = self._make_conn()
        load_response = MagicMock()
        load_response.models = MagicMock()
        load_response.models.current_model_id = ""
        load_response.models.available_models = []
        conn.load_session = AsyncMock(return_value=load_response)

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        # The stale list is cleared (overwritten with []), not preserved.
        assert state.agent_state["acp_available_models"] == []
        # ...and the stale current id is cleared in lock-step: the server
        # reported a ``models`` block with no usable current id, so leaving the
        # old id would render a chip that points at a model absent from the
        # (now-empty) picker list.
        assert "acp_current_model_id" not in state.agent_state

    def test_resume_with_reported_models_but_no_current_clears_stale_id(self, tmp_path):
        """Resume where the server reports a non-empty ``availableModels`` list
        but no usable ``currentModelId`` must CLEAR the stale persisted current
        id while adopting the freshly reported list.

        This is the asymmetric-gating case: ``_available_models`` is reported
        (so the list is overwritten) while ``_current_model_id`` is ``None``.
        The current id must follow the list's "reported" signal, not silently
        keep a stale value the server no longer claims.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "resumable-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_current_model_id": "model-a",
            "acp_available_models": [
                {"model_id": "model-a", "name": "Model A", "description": None},
            ],
        }
        # load_session carries a models block listing models, but with no
        # current selection (e.g. the server cleared its current model).
        conn = self._make_conn()
        load_response = MagicMock()
        load_response.models = MagicMock()
        load_response.models.current_model_id = ""
        model_x = MagicMock()
        model_x.model_id = "model-x"
        model_x.name = "Model X"
        model_x.description = None
        load_response.models.available_models = [model_x]
        conn.load_session = AsyncMock(return_value=load_response)

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        # The stale current id is dropped (server reported none)...
        assert "acp_current_model_id" not in state.agent_state
        # ...while the freshly reported picker list replaces the stale one.
        assert [m["model_id"] for m in state.agent_state["acp_available_models"]] == [
            "model-x"
        ]

    def test_resume_rejected_override_with_absent_models_clears_stale_id(
        self, tmp_path
    ):
        """Resume where ``set_session_model`` is rejected AND ``load_session``
        omits the ``models`` block must CLEAR the stale persisted current id.

        This is the case the preserve-on-resume rule would otherwise keep:
        ``truly_resumed`` is true and ``_available_models`` is ``None`` (server
        didn't report a block), so the only signal that the persisted id is now
        wrong is that we attempted to force ``acp_model`` and the server rejected
        it (``_model_override_applied`` is False). The persisted id named that
        rejected override, so it no longer reflects the live session.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        # ``model-x`` was the authoritative model last launch (applied + persisted).
        agent = _make_agent(acp_model="model-x")
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "resumable-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_current_model_id": "model-x",
        }
        # load_session succeeds (id preserved => truly_resumed) but carries no
        # models block, and the server now rejects the reapply of ``model-x``.
        conn = self._make_conn()
        conn.initialize.return_value.agent_info.name = "codex-acp"
        conn.initialize.return_value.auth_methods = []
        load_response = MagicMock(spec=[])  # no .models block
        conn.load_session = AsyncMock(return_value=load_response)
        conn.set_session_model = AsyncMock(
            side_effect=ACPRequestError(code=-32601, message="method not found")
        )

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        # Resume kept the same session id (so this is a true resume)...
        assert state.agent_state["acp_session_id"] == "resumable-sess"
        # ...the override was not applied, so neither the live attr nor the
        # persisted hint may claim ``model-x``.
        assert agent.current_model_id is None
        assert agent._model_override_applied is False
        assert "acp_current_model_id" not in state.agent_state

    def test_fresh_replacement_clears_stale_model_when_new_session_omits_models(
        self, tmp_path
    ):
        """Fresh replacement (load_session failed → new_session) with no
        ``models`` block in the response must clear the persisted
        ``acp_current_model_*`` rather than carry the old session's values
        forward.

        Otherwise ``acp_session_id`` points at the replacement session while
        the model fields still describe the dead one — ``ConversationInfo``
        renders the wrong chip.
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "stale-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_current_model_id": "claude-opus-4-1",
            "acp_available_models": [
                {"model_id": "claude-opus-4-1", "name": "Opus 4.1"}
            ],
        }
        # load_session fails → new_session runs; its response has no .models.
        new_session_response = MagicMock(spec=["session_id"])
        new_session_response.session_id = "replacement-sess"
        conn = self._make_conn(
            load_exc=ACPRequestError(-32602, "unknown session"),
        )
        conn.new_session = AsyncMock(return_value=new_session_response)

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        # Replacement id wins, and the stale model fields are gone.
        assert state.agent_state["acp_session_id"] == "replacement-sess"
        assert "acp_current_model_id" not in state.agent_state
        assert "acp_available_models" not in state.agent_state

    def test_cwd_mismatch_clears_stale_model_when_new_session_omits_models(
        self, tmp_path
    ):
        """Same contract as the load_session-failure case, but reached via
        the cwd-mismatch branch in ``_start_acp_server`` (which sets
        ``prior_session_id = None`` before falling through to new_session).
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        agent = _make_agent()
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "old-sess",
            "acp_session_cwd": "/some/other/place",
            "acp_current_model_id": "claude-opus-4-1",
            "acp_available_models": [
                {"model_id": "claude-opus-4-1", "name": "Opus 4.1"}
            ],
        }
        new_session_response = MagicMock(spec=["session_id"])
        new_session_response.session_id = "fresh-sess"
        conn = self._make_conn()
        conn.new_session = AsyncMock(return_value=new_session_response)

        agent._executor = AsyncExecutor()
        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        conn.load_session.assert_not_awaited()
        conn.new_session.assert_awaited_once()
        assert state.agent_state["acp_session_id"] == "fresh-sess"
        assert "acp_current_model_id" not in state.agent_state
        assert "acp_available_models" not in state.agent_state

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

    def test_fallback_replacement_clears_suffix_marker(self, tmp_path):
        """If load_session fails, the replacement session has not seen any
        suffix yet, even if the stale session had persisted the marker.
        """
        agent = _make_agent(
            agent_context=AgentContext(system_message_suffix="Team rules.")
        )
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "stale-sess",
            "acp_session_cwd": str(tmp_path),
            "acp_suffix_installed": True,
        }
        conn = self._make_conn(
            new_session_id="replacement-sess",
            load_exc=ACPRequestError(-32602, "unknown session"),
        )

        with self._transport_patches(conn):
            agent.init_state(state, on_event=lambda _: None)

        conn.load_session.assert_awaited_once()
        conn.new_session.assert_awaited_once()
        assert state.agent_state["acp_session_id"] == "replacement-sess"
        assert state.agent_state["acp_session_cwd"] == str(tmp_path)
        assert state.agent_state.get("acp_suffix_installed") is not True
        assert agent._suffix_install_state == "pending_first_prompt"

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
        # Named "codex-acp"; any built-in provider routes acp_model through
        # conn.set_session_model on this path.
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

    @staticmethod
    def _models_block(current_model_id: str, model_ids: list[str]):
        """Build a response ``.models`` block mock for the resolution tests."""
        models = MagicMock()
        models.current_model_id = current_model_id
        entries = []
        for mid in model_ids:
            m = MagicMock()
            m.model_id = mid
            m.name = mid
            m.description = None
            entries.append(m)
        models.available_models = entries
        return models

    def test_unknown_provider_applies_override_via_detected_mechanism(self, tmp_path):
        """Fresh session on an unknown/custom provider with ``acp_model`` set:
        the override is pushed via the mechanism the session advertised — here a
        ``models``-capability response, so ``set_session_model`` — and
        ``current_model_id`` must reflect the applied override.
        """
        agent = _make_agent(acp_model="caller-model")
        state = _make_state(tmp_path)
        new_response = MagicMock()
        new_response.session_id = "fresh-sess"
        new_response.models = self._models_block("server-model", ["server-model"])
        conn = self._make_conn()
        conn.set_config_option = AsyncMock()
        conn.initialize.return_value.agent_info.name = "some-custom-acp"
        conn.initialize.return_value.auth_methods = []
        conn.new_session = AsyncMock(return_value=new_response)

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.set_config_option.assert_not_awaited()
        conn.set_session_model.assert_awaited_once_with(
            model_id="caller-model", session_id="fresh-sess"
        )
        assert agent.current_model_id == "caller-model"

    def test_known_provider_surfaces_applied_override(self, tmp_path):
        """Fresh session on a provider that applies the override via the
        protocol call (codex): ``current_model_id`` reflects the override, since
        it was actually pushed to the server.  Guards the precedence the QA
        verified — the fix must not regress the happy override path.
        """
        agent = _make_agent(acp_model="caller-model")
        state = _make_state(tmp_path)
        new_response = MagicMock()
        new_response.session_id = "fresh-sess"
        new_response.models = self._models_block("server-old", ["server-old"])
        conn = self._make_conn()
        conn.initialize.return_value.agent_info.name = "codex-acp"
        conn.initialize.return_value.auth_methods = []
        conn.new_session = AsyncMock(return_value=new_response)

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.set_session_model.assert_awaited_once_with(
            model_id="caller-model", session_id="fresh-sess"
        )
        assert agent.current_model_id == "caller-model"

    def test_resume_rejected_override_surfaces_server_model(self, tmp_path):
        """Resume where ``set_session_model`` is rejected: the live session keeps
        the server default, so ``current_model_id`` must fall back to what the
        server reported on ``load_session`` rather than claiming the override.
        """
        agent = _make_agent(acp_model="caller-model")
        state = _make_state(tmp_path)
        state.agent_state = {
            **state.agent_state,
            "acp_session_id": "stored-sess",
            "acp_session_cwd": str(tmp_path),
        }
        load_response = MagicMock()
        load_response.models = self._models_block("server-resumed", ["server-resumed"])
        conn = self._make_conn()
        conn.initialize.return_value.agent_info.name = "codex-acp"
        conn.initialize.return_value.auth_methods = []
        conn.load_session = AsyncMock(return_value=load_response)
        # Server rejects the reapply — swallowed, session keeps its own model.
        conn.set_session_model = AsyncMock(
            side_effect=ACPRequestError(code=-32601, message="method not found")
        )

        self._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        assert agent.current_model_id == "server-resumed"

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
    env so the ACP server (Claude Code, Codex CLI, etc.) can use them. They
    reach the subprocess through ``state.secret_registry``: ``LocalConversation``
    seeds ``agent_context.secrets`` into the registry at init (covering
    callers that never lift them into ``request.secrets``), and
    ``_start_acp_server`` injects the registry.
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
    def _run_start_capturing_env(agent, tmp_path, *, state=None) -> dict:
        """Run _start_acp_server and return the env dict passed to the subprocess.

        Pass ``state`` to run against a conversation-seeded registry (e.g. one
        built via ``LocalConversation`` so ``agent_context.secrets`` are lifted
        in); otherwise a bare state is used.
        """
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

        if state is None:
            state = _make_state(tmp_path)
        agent._executor = AsyncExecutor()

        with ExitStack() as stack:
            # Hermetic: exclude the runner's ambient env (e.g. a real
            # GITHUB_TOKEN / ANTHROPIC_API_KEY) so it can't shadow the
            # registry values under test — env.update(os.environ) runs
            # before the fill-if-absent registry tier in _start_acp_server.
            stack.enter_context(patch.dict("os.environ", {}, clear=True))
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
        """A StaticSecret in agent_context.secrets reaches the subprocess env.

        ``LocalConversation`` seeds ``agent_context.secrets`` into
        ``state.secret_registry`` at init, and ``_start_acp_server`` injects the
        registry — the path that delivers ``agent_context.secrets`` to the CLI
        for callers that don't lift them into ``request.secrets`` via
        ``create_request`` (e.g. canvas-local).
        """
        from pydantic import SecretStr

        from openhands.sdk.conversation.impl.local_conversation import (
            LocalConversation,
        )
        from openhands.sdk.secret import StaticSecret

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
        conv = LocalConversation(agent, workspace=str(tmp_path))
        try:
            env = self._run_start_capturing_env(agent, tmp_path, state=conv.state)
        finally:
            conv.close()
        assert env.get("GITHUB_TOKEN") == "ghp_test123"

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

    def test_empty_string_secret_not_injected(self, tmp_path):
        """Empty string secrets are not injected into the subprocess env."""
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={"EMPTY_SECRET": StaticSecret(value=SecretStr(""))}
            )
        )
        env = self._run_start_capturing_env(agent, tmp_path)
        assert "EMPTY_SECRET" not in env


class _BrokenSecret(SecretSource):
    """A source whose ``get_value()`` raises, to verify a failing lookup is
    treated as "skip" rather than taking the subprocess down."""

    def get_value(self) -> str | None:
        raise OSError("network down")


class TestACPSecretRegistryEnvInjection:
    """Tests for secret injection from the conversation's secret_registry.

    Secrets registered via ``Conversation.update_secrets()`` — or the
    equivalent ``payload.secrets`` channel that app-server callers
    (agent-canvas, the OpenHands cloud app server) use — must land in the
    ACP subprocess env. ``agent_context.secrets`` are seeded into the same
    registry at ``LocalConversation.__init__`` (below ``request.secrets``), so
    the registry is the single channel ``_start_acp_server`` injects from.

    Same-key precedence is ``secret_registry > os.environ``.
    Registry secrets override ambient ``os.environ`` so an explicit
    per-conversation/provider secret wins over a same-named server env var.
    """

    @staticmethod
    def _run_start_capturing_env(
        agent, tmp_path, *, registry_secrets=None, extra_os_env=None, state=None
    ) -> dict:
        """Re-uses the env-capture harness from TestACPSecretsEnvInjection.

        Pass ``state`` to run against a conversation-seeded registry (e.g. one
        built via ``LocalConversation`` so ``agent_context.secrets`` are lifted
        in); otherwise a bare state is used and ``registry_secrets`` are applied
        directly.
        """
        if state is None:
            state = _make_state(tmp_path)
        if registry_secrets:
            state.secret_registry.update_secrets(registry_secrets)

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

        agent._executor = AsyncExecutor()

        with ExitStack() as stack:
            # Hermetic: replace the runner's ambient env with extra_os_env (or
            # nothing), so it can't shadow the registry values under test and so
            # tests can inject a controlled ambient var to assert precedence.
            stack.enter_context(
                patch.dict("os.environ", extra_os_env or {}, clear=True)
            )
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

    def test_registry_string_secret_injected_into_subprocess_env(self, tmp_path):
        """A string secret in secret_registry lands in the subprocess env.

        The canvas / OpenHands ``payload.secrets`` channel ends up here
        via ``Conversation.update_secrets()`` → ``SecretRegistry.update_secrets``;
        without this injection the secret is invisible to the ACP CLI.
        """
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"ANTHROPIC_API_KEY": "sk-from-registry"},
        )
        assert env.get("ANTHROPIC_API_KEY") == "sk-from-registry"

    def test_registry_lookup_secret_injected_into_subprocess_env(self, tmp_path):
        """A LookupSecret (callable) in secret_registry resolves and injects.

        This is the wire shape canvas actually sends: a ``LookupSecret``
        whose ``get_value()`` fetches over HTTP from the agent-server's
        ``/api/settings/secrets/{name}`` endpoint.
        """
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={
                "OPENAI_API_KEY": _FakeLookupSecret(stored_value="sk-fake-openai")
            },
        )
        assert env.get("OPENAI_API_KEY") == "sk-fake-openai"

    def test_request_secret_wins_and_context_only_secret_still_reaches_env(
        self, tmp_path
    ):
        """request.secrets win on collision; a context-only key still reaches env.

        Both channels flow through ``state.secret_registry``: ``LocalConversation``
        seeds ``agent_context.secrets`` first, then ``request.secrets`` overwrite
        colliding keys. A key present only in ``agent_context.secrets`` still
        lands in the registry — and therefore the subprocess env — proving the
        two channels coexist without one dropping the other.
        """
        from pydantic import SecretStr

        from openhands.sdk.conversation.impl.local_conversation import (
            LocalConversation,
        )
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={
                    "GITHUB_TOKEN": StaticSecret(value=SecretStr("from-context")),
                    "CONTEXT_ONLY": StaticSecret(value=SecretStr("ctx-value")),
                }
            )
        )
        conv = LocalConversation(
            agent,
            workspace=str(tmp_path),
            secrets={"GITHUB_TOKEN": StaticSecret(value=SecretStr("from-request"))},
        )
        try:
            env = self._run_start_capturing_env(agent, tmp_path, state=conv.state)
        finally:
            conv.close()
        assert env.get("GITHUB_TOKEN") == "from-request"
        assert env.get("CONTEXT_ONLY") == "ctx-value"

    def test_empty_registry_does_not_change_behaviour(self, tmp_path):
        """An empty secret_registry must not raise or alter the spawn env."""
        agent = _make_agent()
        env = self._run_start_capturing_env(agent, tmp_path, registry_secrets=None)
        # No secrets to inject; spawn still succeeds and produces an env dict.
        assert isinstance(env, dict)

    def test_failing_registry_lookup_swallowed(self, tmp_path):
        """A secret source that raises is dropped, not propagated.

        ``SecretRegistry.get_secret_value`` already catches lookup
        errors and returns ``None``; the spawn-env loop must treat
        that ``None`` as "skip", so a transient secret-source failure
        (network blip, expired token) doesn't take the whole ACP
        subprocess down.
        """
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"BROKEN": _BrokenSecret()},
        )
        assert "BROKEN" not in env

    def test_registry_secret_overrides_ambient_os_environ(self, tmp_path):
        """A registry secret overrides a same-named ambient os.environ var.

        Conversation/provider creds must win over the agent-server's own
        environment (os.environ is the wrong process for a remote server).
        Before this change the ambient value silently won.
        """
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"ANTHROPIC_API_KEY": "from-registry"},
            extra_os_env={"ANTHROPIC_API_KEY": "ambient-should-lose"},
        )
        assert env.get("ANTHROPIC_API_KEY") == "from-registry"

    def test_agent_context_secret_overrides_ambient_os_environ(self, tmp_path):
        """An agent_context secret (seeded into the registry) beats ambient os.environ.

        ``LocalConversation`` lifts ``agent_context.secrets`` into the registry,
        and registry secrets override the agent-server's own ``os.environ`` so a
        per-conversation/provider secret wins over a same-named server env var.
        """
        from pydantic import SecretStr

        from openhands.sdk.conversation.impl.local_conversation import (
            LocalConversation,
        )
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={"GITHUB_TOKEN": StaticSecret(value=SecretStr("from-context"))}
            )
        )
        conv = LocalConversation(agent, workspace=str(tmp_path))
        try:
            env = self._run_start_capturing_env(
                agent,
                tmp_path,
                extra_os_env={"GITHUB_TOKEN": "ambient-should-lose"},
                state=conv.state,
            )
        finally:
            conv.close()
        assert env.get("GITHUB_TOKEN") == "from-context"


class TestACPEnvConflictSuppression:
    """An active CLAUDE_CODE_OAUTH_TOKEN must not coexist with API-key env vars.

    When CLAUDE_CODE_OAUTH_TOKEN is present the subprocess authenticates with
    that bearer against api.anthropic.com.  A co-present ANTHROPIC_API_KEY would
    take precedence (bypassing the subscription) and an ANTHROPIC_BASE_URL would
    route the bearer to a proxy that rejects it, breaking auth silently.

    _start_acp_server must strip the conflicting vars regardless of where they
    came from: os.environ, secret_registry, or agent_context.secrets.
    The strip is keyed on the token, not on CLAUDE_CONFIG_DIR (#3588).
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
        new_response.session_id = "sess-conflict"
        conn.new_session = AsyncMock(return_value=new_response)
        conn.load_session = AsyncMock(return_value=MagicMock())
        conn.set_session_mode = AsyncMock()
        conn.set_session_model = AsyncMock()
        conn.authenticate = AsyncMock()
        conn.close = AsyncMock()
        return conn

    @staticmethod
    def _run_start_capturing_env(
        agent, tmp_path, *, extra_os_env=None, registry_secrets=None
    ) -> dict:
        from contextlib import ExitStack

        from openhands.sdk.utils.async_executor import AsyncExecutor

        captured: dict = {}
        conn = TestACPEnvConflictSuppression._make_conn()

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()

        async def _fake_create_subprocess_exec(*_args, env=None, **_kwargs):
            captured.update(env or {})
            return mock_process

        async def _fake_filter(_src, _dst):
            return None

        state = _make_state(tmp_path)
        if registry_secrets:
            state.secret_registry.update_secrets(registry_secrets)
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
            # Hermetic: clear the runner's ambient env so it can't shadow the
            # values under test; extra_os_env is the only os.environ content
            # (used by the test that injects ANTHROPIC_API_KEY via os.environ).
            stack.enter_context(
                patch.dict("os.environ", extra_os_env or {}, clear=True)
            )
            agent._start_acp_server(state)

        return captured

    def test_oauth_token_suppresses_api_key_from_os_environ(self, tmp_path):
        """ANTHROPIC_API_KEY leaking in from os.environ is stripped too."""
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok"},
            extra_os_env={
                "ANTHROPIC_API_KEY": "sk-leaked",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            },
        )

        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_oauth_token_suppresses_api_key_from_registry(self, tmp_path):
        """The token + conflicting vars all injected via secret_registry.

        This is the channel provider creds now travel on (folded into
        ``agent_context.secrets`` by ``create_agent`` → lifted into the
        registry by ``create_request``).
        """
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={
                "CLAUDE_CODE_OAUTH_TOKEN": "oauth-from-registry",
                "ANTHROPIC_API_KEY": "sk-from-registry",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            },
        )

        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-from-registry"
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_oauth_token_suppresses_api_key_from_secrets(self, tmp_path):
        """Conflicting vars drained from agent_context.secrets are stripped too.

        Covers the canvas-local channel: provider creds folded into
        ``agent_context.secrets`` reach env via the drain, and must still be
        stripped when the OAuth token is active.
        """
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={
                    "ANTHROPIC_API_KEY": StaticSecret(
                        value=SecretStr("sk-from-secret")
                    ),
                    "ANTHROPIC_BASE_URL": StaticSecret(
                        value=SecretStr("https://proxy.example.com")
                    ),
                }
            ),
        )
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok"},
        )

        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_no_suppression_without_oauth_token(self, tmp_path):
        """Without the OAuth token, ANTHROPIC_API_KEY passes through unchanged."""
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"ANTHROPIC_API_KEY": "sk-valid"},
        )

        assert env.get("ANTHROPIC_API_KEY") == "sk-valid"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env

    def test_config_dir_alone_does_not_suppress_api_key(self, tmp_path):
        """Regression (#3588): CLAUDE_CONFIG_DIR without the OAuth token must NOT
        strip ANTHROPIC_API_KEY. The config dir is a location lever (data-dir
        isolation), orthogonal to auth mode — keying the strip on it used to
        delete a working API key during isolation."""
        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={
                "CLAUDE_CONFIG_DIR": "/tmp/claude-isolated",
                "ANTHROPIC_API_KEY": "sk-valid",
            },
        )

        assert env["CLAUDE_CONFIG_DIR"] == "/tmp/claude-isolated"
        assert env.get("ANTHROPIC_API_KEY") == "sk-valid"


class TestACPAgentCurrentModelIdProperty:
    """``current_model_id`` is a read-only property backed by a PrivateAttr.

    ``AgentBase`` is frozen so the value can't live on the agent as a
    regular Pydantic field; it doesn't round-trip through ``model_dump``
    either.  Cross-process consumers (the OpenHands app_server) should
    read it off ``ConversationInfo`` instead — the agent-server lifts the
    value off the agent into the API response.
    """

    def test_defaults_to_none(self):
        agent = _make_agent()
        assert agent.current_model_id is None

    def test_reflects_private_attr(self):
        # ``_init`` writes the resolved model into ``_current_model_id``
        # after consulting the server response + the caller override.
        agent = _make_agent()
        agent._current_model_id = "claude-sonnet-4-5"
        assert agent.current_model_id == "claude-sonnet-4-5"

    def test_acp_model_override_wins_over_server_report(self):
        """When ``acp_model`` is set, ``current_model_id`` reflects the override.

        Mirrors the resolution logic in ``_init``: a caller-provided
        ``acp_model`` takes precedence over whatever the server happens to
        report — both for the ``set_session_model`` path (Codex / Gemini)
        and the ``session _meta`` path (Claude Code).
        """
        agent = _make_agent(acp_model="gpt-5")
        agent._current_model_id = agent.acp_model or "fallback-from-server"
        assert agent.current_model_id == "gpt-5"

    def test_does_not_round_trip_through_json(self):
        # Locks in the deliberate design choice: PrivateAttr → not serialized.
        # Cross-process consumers must read from ``ConversationInfo``.
        agent = _make_agent()
        agent._current_model_id = "claude-opus-4-1"
        clone = ACPAgent.model_validate_json(agent.model_dump_json())
        assert clone.current_model_id is None


class TestExtractSessionModels:
    """``_extract_session_models`` reads the model the ACP server reports.

    The ``models`` capability is marked UNSTABLE in the spec. The second
    element distinguishes **absent** (``None`` — block missing) from
    **present-but-empty** (``[]`` — server reports no models), which the
    resume-persistence logic relies on to preserve vs. clear the stored list.
    """

    def test_returns_both_when_response_carries_them(self):
        m1 = MagicMock()
        m1.model_id = "default"
        m1.name = "Default (recommended)"
        m1.description = "Opus 4.7 with 1M context · Most capable"
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = "default"
        response.models.available_models = [m1]
        cur, avail, _ = _extract_session_models(response)
        assert cur == "default"
        # Normalized into our stable ACPModelInfo, not the raw acp type.
        assert avail == [
            ACPModelInfo(
                model_id="default",
                name="Default (recommended)",
                description="Opus 4.7 with 1M context · Most capable",
            )
        ]

    def test_returns_none_list_when_models_block_absent(self):
        # Older agents don't include the ``models`` block at all -> None, so
        # callers know nothing was reported (and can preserve prior state).
        response = MagicMock(spec=[])
        cur, avail, _ = _extract_session_models(response)
        assert cur is None
        assert avail is None

    def test_returns_empty_list_when_available_models_missing(self):
        # ``models`` block present but ``availableModels`` absent/None: the
        # block WAS reported, so we return ``[]`` (present, no models), not None.
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = "gpt-5"
        response.models.available_models = None
        cur, avail, _ = _extract_session_models(response)
        assert cur == "gpt-5"
        assert avail == []

    def test_returns_none_list_when_response_is_none(self):
        # ``load_session`` can return ``None`` for servers that don't
        # implement the call — the helper must not crash, and reports "absent".
        assert _extract_session_models(None) == (None, None, False)

    def test_returns_none_list_when_models_field_is_none(self):
        response = MagicMock()
        response.models = None
        assert _extract_session_models(response) == (None, None, False)

    def test_returns_none_when_current_model_id_is_empty_string(self):
        # An empty string is treated the same as a missing field — we don't
        # want to surface "" as a real model name. The block is present, so
        # available_models is [] (not None).
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = ""
        response.models.available_models = []
        assert _extract_session_models(response) == (None, [], False)

    def test_returns_none_when_current_model_id_is_not_a_string(self):
        # Defensive: an agent returning a non-string here is malformed.
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = 42
        response.models.available_models = []
        assert _extract_session_models(response) == (None, [], False)


class TestExtractSessionModelsNormalization:
    """``_extract_session_models`` normalizes raw acp entries to ACPModelInfo.

    The SDK deliberately re-maps the (UNSTABLE) ``acp.schema`` ``ModelInfo``
    into our own stable type at this boundary, tolerating partial/malformed
    entries rather than leaking the vendored shape or raising.
    """

    def _raw(self, model_id: Any, name: Any = None, description: Any = None) -> Any:
        m = MagicMock()
        m.model_id = model_id
        m.name = name
        m.description = description
        return m

    def test_maps_fields_through(self):
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = "gpt-5.4"
        response.models.available_models = [
            self._raw("gpt-5.4", "GPT-5.4", "Strong everyday model."),
        ]
        _cur, avail, _ = _extract_session_models(response)
        assert avail == [
            ACPModelInfo(
                model_id="gpt-5.4",
                name="GPT-5.4",
                description="Strong everyday model.",
            )
        ]

    def test_drops_entries_without_usable_id(self):
        # A malformed entry (missing/non-string id) must not blow up session
        # bring-up, and must not surface as an empty-id picker option — it's
        # dropped, while valid entries alongside it survive.
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = "good"
        response.models.available_models = [
            self._raw(model_id=42),  # non-string -> "" -> dropped
            self._raw(model_id="good", name="Good"),
            self._raw(model_id="", name="Empty"),  # empty -> dropped
        ]
        _cur, avail, _ = _extract_session_models(response)
        assert avail == [ACPModelInfo(model_id="good", name="Good", description=None)]


class TestConfigOptionModelMechanism:
    """Model selection via the ``model`` ``configOptions`` select.

    codex-acp 0.16+ and claude-agent-acp 0.44+ dropped the UNSTABLE ``models``
    capability + ``session/set_model`` in favour of a ``model`` config-option
    select driven by ``session/set_config_option``. ``_extract_session_models``
    reads that select and reports the apply mechanism as its third return value
    (``via_config_option``) in the same scan.
    """

    def _select(self, *, id="model", type="select", current_value, options):
        opt = SimpleNamespace(
            id=id, type=type, current_value=current_value, options=options
        )
        return opt

    def _opt(self, value, name=None, description=None):
        return SimpleNamespace(value=value, name=name, description=description)

    def _response(self, *, models=None, config_options):
        return SimpleNamespace(models=models, config_options=config_options)

    def test_extracts_model_state_from_config_option(self):
        # claude-agent-acp 0.44: model select with short aliases.
        response = self._response(
            config_options=[
                self._select(
                    current_value="opus[1m]",
                    options=[
                        self._opt("default", "Default (recommended)", "Opus 4.8 · 1M"),
                        self._opt("opus[1m]", "Opus", "Opus 4.8 · 1M"),
                        self._opt("sonnet", "Sonnet", "Sonnet 4.6"),
                        self._opt("haiku", "Haiku", "Haiku 4.5"),
                    ],
                )
            ],
        )
        cur, avail, via = _extract_session_models(response)
        assert cur == "opus[1m]"
        assert via is True
        assert avail == [
            ACPModelInfo(
                model_id="default",
                name="Default (recommended)",
                description="Opus 4.8 · 1M",
            ),
            ACPModelInfo(model_id="opus[1m]", name="Opus", description="Opus 4.8 · 1M"),
            ACPModelInfo(model_id="sonnet", name="Sonnet", description="Sonnet 4.6"),
            ACPModelInfo(model_id="haiku", name="Haiku", description="Haiku 4.5"),
        ]

    def test_unwraps_rootmodel_wrapped_config_option(self):
        # agent-client-protocol 0.8.x wraps each entry in a SessionConfigOption
        # RootModel (the option lives under ``.root``); detection must unwrap it,
        # else ``opt.id`` is None and the SDK wrongly falls back to set_model.
        select = self._select(
            current_value="opus[1m]",
            options=[self._opt("opus[1m]", "Opus"), self._opt("sonnet", "Sonnet")],
        )
        wrapped = SimpleNamespace(root=select)  # mimics the RootModel wrapper
        response = self._response(config_options=[wrapped])
        cur, avail, via = _extract_session_models(response)
        assert via is True
        assert cur == "opus[1m]"
        assert avail is not None
        assert [m.model_id for m in avail] == ["opus[1m]", "sonnet"]

    def test_models_capability_wins_over_config_option(self):
        # If a server somehow carries both, the ``models`` capability is used.
        models = MagicMock()
        models.current_model_id = "from-models"
        models.available_models = []
        response = self._response(
            models=models,
            config_options=[
                self._select(
                    current_value="from-config", options=[self._opt("from-config", "X")]
                )
            ],
        )
        cur, avail, via = _extract_session_models(response)
        assert cur == "from-models"
        assert avail == []
        assert via is False

    def test_detects_config_option_mechanism(self):
        response = self._response(
            config_options=[
                self._select(
                    current_value="gpt-5.5", options=[self._opt("gpt-5.5", "GPT-5.5")]
                )
            ],
        )
        assert _extract_session_models(response)[2] is True

    def test_ignores_non_model_config_options(self):
        # A ``mode`` select alongside no ``model`` select ⇒ old mechanism, and
        # the default (False) is reported since neither block was present.
        response = self._response(
            config_options=[
                SimpleNamespace(
                    id="mode",
                    type="select",
                    current_value="default",
                    options=[self._opt("default", "Default")],
                ),
            ],
        )
        assert _extract_session_models(response) == (None, None, False)

    def test_drops_options_without_usable_value(self):
        response = self._response(
            config_options=[
                self._select(
                    current_value="ok",
                    options=[
                        self._opt(value=42),  # non-string -> dropped
                        self._opt("ok", "Ok"),
                        self._opt(value="", name="Empty"),  # empty -> dropped
                    ],
                )
            ],
        )
        cur, avail, _ = _extract_session_models(response)
        assert cur == "ok"
        assert avail == [ACPModelInfo(model_id="ok", name="Ok", description=None)]

    def test_none_response_is_not_config_option(self):
        assert _extract_session_models(None)[2] is False

    def test_default_via_config_option_used_when_no_block_present(self):
        # On resume a load_session that omits both the models capability and the
        # model config-option must not blindly default to set_session_model: the
        # caller passes the persisted mechanism hint, which is honored (#3772).
        response = self._response(config_options=[])
        assert _extract_session_models(response)[2] is False
        assert (
            _extract_session_models(response, default_via_config_option=True)[2] is True
        )
        # A present block still wins over the hint.
        present = self._response(
            config_options=[
                self._select(current_value="x", options=[self._opt("x", "X")])
            ]
        )
        assert (
            _extract_session_models(present, default_via_config_option=False)[2] is True
        )


# Real ``session/new`` wire payloads (by-alias) captured from the pinned CLIs.
# Parsed through the real ``NewSessionResponse`` so the test exercises the
# genuine schema, INCLUDING the ``SessionConfigOption`` RootModel wrapper the
# ``agent-client-protocol`` lib applies to ``config_options`` on 0.8.x (where a
# naive ``opt.id`` read returns ``None``). The detection helper unwraps
# ``.root`` so it works on 0.8.x + 0.10.x.
def _select_dict(opt_id, current, values, category=None):
    return {
        "id": opt_id,
        "name": opt_id,
        "type": "select",
        "category": category,
        "currentValue": current,
        "options": [{"value": v, "name": v} for v in values],
    }


_CLAUDE_046_SESSION = {
    "sessionId": "sess-claude",
    "models": None,
    "configOptions": [
        _select_dict("mode", "default", ["default", "acceptEdits"]),
        _select_dict(
            "model",
            "opus[1m]",
            ["default", "opus[1m]", "sonnet", "haiku"],
            category="model",
        ),
        _select_dict("effort", "xhigh", ["xhigh", "low"]),
    ],
}
_CODEX_016_SESSION = {
    "sessionId": "sess-codex",
    "models": None,
    "configOptions": [
        _select_dict("mode", "read-only", ["read-only", "full-access"]),
        _select_dict(
            "model",
            "gpt-5.5",
            ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
            category="model",
        ),
        _select_dict("reasoning_effort", "xhigh", ["xhigh", "low"]),
    ],
}
_GEMINI_046_SESSION = {
    "sessionId": "sess-gemini",
    "configOptions": None,
    "models": {
        "currentModelId": "gemini-3-flash-preview",
        "availableModels": [
            {"modelId": mid, "name": mid}
            for mid in (
                "auto",
                "gemini-3-pro-preview",
                "gemini-3-flash-preview",
                "gemini-2.5-pro",
                "gemini-2.5-flash",
                "gemini-3.1-flash-lite",
            )
        ],
    },
}


class TestDetectionAgainstRealSessionResponses:
    """Detection + extraction against the real CLI ``session/new`` wire shapes,
    parsed through the actual ``acp.schema.NewSessionResponse``.

    Guards a schema-shape regression: on ``agent-client-protocol`` 0.8.x each
    ``config_options`` entry is a ``SessionConfigOption`` RootModel (``.root``),
    so a naive ``opt.id`` read is ``None`` and the SDK would fall back to the
    removed ``session/set_model``.
    """

    def test_config_options_is_the_schema_field_name(self):
        # The SDK reads ``response.config_options``; pin that this is the real
        # field on the schema.
        assert "config_options" in NewSessionResponse.model_fields

    def test_claude_046_uses_config_option(self):
        resp = NewSessionResponse.model_validate(_CLAUDE_046_SESSION)
        cur, avail, via = _extract_session_models(resp)
        assert via is True
        assert cur == "opus[1m]"
        assert avail is not None
        assert [m.model_id for m in avail] == ["default", "opus[1m]", "sonnet", "haiku"]

    def test_codex_016_uses_config_option(self):
        resp = NewSessionResponse.model_validate(_CODEX_016_SESSION)
        cur, avail, via = _extract_session_models(resp)
        assert via is True
        assert cur == "gpt-5.5"
        assert avail is not None
        assert [m.model_id for m in avail] == ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"]

    def test_gemini_046_uses_set_session_model(self):
        resp = NewSessionResponse.model_validate(_GEMINI_046_SESSION)
        cur, avail, via = _extract_session_models(resp)
        assert via is False
        assert cur == "gemini-3-flash-preview"
        assert avail is not None
        assert "gemini-3-pro-preview" in [m.model_id for m in avail]


class TestApplyAcpModelNoFallback:
    """``_apply_acp_model`` applies via the advertised mechanism only — there is
    no cross-mechanism fallback. Response-detection is deterministic from the
    session/new shape and correct for every validated CLI, so any rejection is a
    real error (invalid model id, or a genuinely mis-advertised server) and
    propagates rather than silently trying the other call.
    """

    @pytest.mark.asyncio
    async def test_config_option_rejection_propagates(self):
        conn = AsyncMock()
        conn.set_config_option.side_effect = ACPRequestError(
            code=-32601, message="Method not found"
        )
        with pytest.raises(ACPRequestError):
            await _apply_acp_model(conn, "sess-1", "opus[1m]", via_config_option=True)
        conn.set_session_model.assert_not_called()  # no fallback

    @pytest.mark.asyncio
    async def test_set_session_model_rejection_propagates(self):
        conn = AsyncMock()
        conn.set_session_model.side_effect = ACPRequestError(
            code=-32601, message="Method not found"
        )
        with pytest.raises(ACPRequestError):
            await _apply_acp_model(
                conn, "sess-1", "gemini-3-flash", via_config_option=False
            )
        conn.set_config_option.assert_not_called()  # no fallback


class TestApplyAcpModel:
    """``_apply_acp_model`` uses the mechanism the session advertised."""

    @pytest.mark.asyncio
    async def test_config_option_single_call(self):
        conn = AsyncMock()
        await _apply_acp_model(conn, "sess-1", "gpt-5.5", via_config_option=True)
        conn.set_config_option.assert_awaited_once_with(
            config_id="model", value="gpt-5.5", session_id="sess-1"
        )
        conn.set_session_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_session_model_branch(self):
        conn = AsyncMock()
        await _apply_acp_model(
            conn, "sess-1", "gemini-2.5-pro", via_config_option=False
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="gemini-2.5-pro", session_id="sess-1"
        )
        conn.set_config_option.assert_not_called()

    @pytest.mark.asyncio
    async def test_codex_config_option_splits_reasoning_effort(self):
        conn = AsyncMock()
        await _apply_acp_model(
            conn,
            "sess-1",
            "gpt-5.5/xhigh",
            agent_name="codex-acp",
            via_config_option=True,
        )
        conn.set_config_option.assert_has_awaits(
            [
                call(config_id="model", value="gpt-5.5", session_id="sess-1"),
                call(
                    config_id="reasoning_effort",
                    value="xhigh",
                    session_id="sess-1",
                ),
            ]
        )
        assert conn.set_config_option.await_count == 2
        conn.set_session_model.assert_not_called()


class TestACPAgentAvailableModelsProperty:
    """``available_models`` exposes the server's model list verbatim.

    No server-side curation: the property hands back the normalized
    ``ACPModelInfo`` list so clients render the picker and resolve
    ``current_model_id`` to a display label themselves.
    """

    def test_defaults_to_empty(self):
        assert _make_agent().available_models == []

    def test_reflects_private_attr(self):
        agent = _make_agent()
        models = [
            ACPModelInfo(
                model_id="default",
                name="Default (recommended)",
                description="Opus 4.7 with 1M context · Most capable",
            ),
            ACPModelInfo(model_id="sonnet", name="Sonnet"),
        ]
        agent._available_models = models
        assert agent.available_models == models

    def test_returns_a_copy(self):
        # Mutating the returned list must not corrupt the agent's state.
        agent = _make_agent()
        agent._available_models = [ACPModelInfo(model_id="default")]
        got = agent.available_models
        got.append(ACPModelInfo(model_id="injected"))
        assert [m.model_id for m in agent.available_models] == ["default"]


class TestACPAgentSupportsRuntimeModelSwitch:
    """``supports_runtime_model_switch`` gates the live-switch picker.

    ``True`` only for known providers that declare ``session/set_model`` support.
    Unknown/custom providers use ``set_config_option`` for initial model selection
    but that is a generic config write, not a guaranteed live-switch primitive,
    so the picker is hidden for them. ``False`` before a session exists.
    """

    def test_false_before_session(self):
        # No live session (``_session_id is None``) -> nothing to switch.
        agent = _make_agent()
        agent._agent_name = "codex-acp"
        assert agent.supports_runtime_model_switch is False

    def test_true_for_known_switch_capable_provider(self):
        agent = _make_agent()
        agent._session_id = "sess-1"
        agent._agent_name = "codex-acp"
        assert agent.supports_runtime_model_switch is True

    def test_false_for_unknown_provider(self):
        # Unknown/custom providers use set_config_option for initial model
        # selection only; the live-switch picker is hidden for them.
        agent = _make_agent()
        agent._session_id = "sess-1"
        agent._agent_name = "some-third-party-acp-server"
        assert agent.supports_runtime_model_switch is False

    def test_false_for_known_unsupported_provider(self, monkeypatch):
        # A known provider that declares no support is the one case we refuse.
        import openhands.sdk.agent.acp_agent as acp_agent_module

        unsupported = MagicMock()
        unsupported.supports_runtime_model_switch = False
        monkeypatch.setattr(
            acp_agent_module,
            "detect_acp_provider_by_agent_name",
            lambda _name: unsupported,
        )
        agent = _make_agent()
        agent._session_id = "sess-1"
        agent._agent_name = "locked-down-provider"
        assert agent.supports_runtime_model_switch is False


# ---------------------------------------------------------------------------

# MCP forwarding
# ---------------------------------------------------------------------------


class TestMcpConfigToAcpServers:
    """Unit tests for the mcp_config -> ACP server translation + gating."""

    @staticmethod
    def _caps(http: bool, sse: bool):
        from acp.schema import McpCapabilities

        return McpCapabilities(http=http, sse=sse)

    @staticmethod
    def _config(config: Mapping[str, object]):
        servers = config.get("mcpServers", config)
        return coerce_mcp_config(servers)

    def test_stdio_always_forwarded(self):
        from acp.schema import McpServerStdio

        cfg = {
            "mcpServers": {
                "fetch": {
                    "command": "uvx",
                    "args": ["mcp-server-fetch"],
                    "env": {"API_KEY": "x"},
                }
            }
        }
        # Even with no advertised remote capabilities, stdio is forwarded.
        out = _mcp_config_to_acp_servers(
            self._config(cfg), self._caps(http=False, sse=False)
        )
        assert len(out) == 1
        srv = out[0]
        assert isinstance(srv, McpServerStdio)
        assert srv.name == "fetch"
        assert srv.command == "uvx"
        assert srv.args == ["mcp-server-fetch"]
        assert [(e.name, e.value) for e in srv.env] == [("API_KEY", "x")]

    def test_http_gated_on_capability(self):
        from acp.schema import HttpMcpServer

        cfg = {
            "mcpServers": {
                "remote": {
                    "url": "https://h/mcp",
                    "headers": {"Authorization": "Bearer y"},
                }
            }
        }
        # Dropped when the server doesn't advertise http.
        assert (
            _mcp_config_to_acp_servers(
                self._config(cfg), self._caps(http=False, sse=False)
            )
            == []
        )
        # Forwarded when advertised.
        out = _mcp_config_to_acp_servers(
            self._config(cfg), self._caps(http=True, sse=False)
        )
        assert len(out) == 1
        assert isinstance(out[0], HttpMcpServer)
        assert out[0].type == "http"
        assert out[0].url == "https://h/mcp"
        assert [(h.name, h.value) for h in out[0].headers] == [
            ("Authorization", "Bearer y")
        ]

    def test_http_auth_maps_to_bearer_header(self):
        from acp.schema import HttpMcpServer

        cfg = {
            "mcpServers": {
                "remote": {
                    "url": "https://h/mcp",
                    "auth": {"strategy": "bearer", "value": "token-y"},
                }
            }
        }
        out = _mcp_config_to_acp_servers(
            self._config(cfg), self._caps(http=True, sse=False)
        )
        assert len(out) == 1
        assert isinstance(out[0], HttpMcpServer)
        assert [(h.name, h.value) for h in out[0].headers] == [
            ("Authorization", "Bearer token-y")
        ]

    def test_http_header_auth_forwards_authorization_header(self):
        cfg = {
            "mcpServers": {
                "remote": {
                    "url": "https://h/mcp",
                    "auth": {
                        "strategy": "header",
                        "headers": {"authorization": "Bearer explicit"},
                    },
                }
            }
        }
        out = _mcp_config_to_acp_servers(
            self._config(cfg), self._caps(http=True, sse=False)
        )
        assert [(h.name, h.value) for h in out[0].headers] == [
            ("authorization", "Bearer explicit")
        ]

    def test_sse_gated_on_capability(self):
        from acp.schema import SseMcpServer

        cfg = {"mcpServers": {"s": {"url": "https://s/sse", "transport": "sse"}}}
        assert (
            _mcp_config_to_acp_servers(
                self._config(cfg), self._caps(http=True, sse=False)
            )
            == []
        )
        out = _mcp_config_to_acp_servers(
            self._config(cfg), self._caps(http=True, sse=True)
        )
        assert len(out) == 1
        assert isinstance(out[0], SseMcpServer)
        assert out[0].type == "sse"

    def test_streamable_http_maps_to_http(self):
        from acp.schema import HttpMcpServer

        cfg = {
            "mcpServers": {
                "s": {"url": "https://h/mcp", "transport": "streamable-http"}
            }
        }
        out = _mcp_config_to_acp_servers(
            self._config(cfg), self._caps(http=True, sse=True)
        )
        assert len(out) == 1
        assert isinstance(out[0], HttpMcpServer)

    def test_empty_configs(self):
        caps = self._caps(http=True, sse=True)
        assert _mcp_config_to_acp_servers({}, caps) == []
        assert _mcp_config_to_acp_servers(self._config({"mcpServers": {}}), caps) == []

    def test_none_capabilities_drops_remote_keeps_stdio(self):
        from acp.schema import McpServerStdio

        cfg = {
            "mcpServers": {
                "fetch": {"command": "echo"},
                "remote": {"url": "https://h/mcp"},
            }
        }
        out = _mcp_config_to_acp_servers(self._config(cfg), None)
        assert [type(s).__name__ for s in out] == [McpServerStdio.__name__]


class TestACPMcpForwarding:
    """The translated servers reach new_session AND load_session (resume)."""

    @staticmethod
    def _conn_with_caps(*, http=True, sse=True, load_exc=None):
        conn = TestACPSessionIdPersistence._make_conn(load_exc=load_exc)
        conn.initialize.return_value.agent_capabilities.mcp_capabilities = (
            TestMcpConfigToAcpServers._caps(http=http, sse=sse)
        )
        return conn

    def test_new_session_receives_acp_mcp_servers(self, tmp_path):
        agent = _make_agent(mcp_config={"fetch": {"command": "echo"}})
        state = _make_state(tmp_path)
        conn = self._conn_with_caps()

        TestACPSessionIdPersistence._patched_start_acp_server(agent, state, conn=conn)

        conn.new_session.assert_awaited_once()
        servers = conn.new_session.call_args.kwargs["mcp_servers"]
        assert [s.name for s in servers] == ["fetch"]

    def test_resume_load_session_receives_acp_mcp_servers(self, tmp_path):
        """The key correctness point: resume must re-pass MCP servers, since
        load_session does not persist them server-side."""
        agent = _make_agent(mcp_config={"fetch": {"command": "echo"}})
        state = _make_state(tmp_path)
        state.agent_state = {**state.agent_state, "acp_session_id": "stored-sess"}
        conn = self._conn_with_caps()

        TestACPSessionIdPersistence._patched_start_acp_server(agent, state, conn=conn)

        conn.load_session.assert_awaited_once()
        servers = conn.load_session.call_args.kwargs["mcp_servers"]
        assert [s.name for s in servers] == ["fetch"]
        conn.new_session.assert_not_awaited()

    def test_no_mcp_config_forwards_empty_acp_mcp_servers_list(self, tmp_path):
        agent = _make_agent()
        state = _make_state(tmp_path)
        conn = self._conn_with_caps()

        TestACPSessionIdPersistence._patched_start_acp_server(agent, state, conn=conn)

        conn.new_session.assert_awaited_once()
        assert conn.new_session.call_args.kwargs["mcp_servers"] == []


# ---------------------------------------------------------------------------
# Reserved file-content secret materialisation (issue #1020)
# ---------------------------------------------------------------------------


class TestACPFileSecretMaterialisation:
    """Codex auth.json / Gemini Vertex SA JSON materialise to the durable
    per-conversation root, with the right data-dir env var, seed-if-absent.
    """

    @staticmethod
    def _make_conn(*, agent_name: str = "codex-acp", auth_method: str | None = None):
        conn = MagicMock()
        init_response = MagicMock()
        init_response.agent_info = MagicMock()
        init_response.agent_info.name = agent_name
        init_response.agent_info.version = "1.0"
        init_response.auth_methods = [MagicMock(id=auth_method)] if auth_method else []
        # MagicMock(id=...) doesn't set .id; assign explicitly.
        for m in init_response.auth_methods:
            m.id = auth_method
        conn.initialize = AsyncMock(return_value=init_response)
        new_response = MagicMock()
        new_response.session_id = "sess-new"
        new_response.models = None
        conn.new_session = AsyncMock(return_value=new_response)
        conn.load_session = AsyncMock(return_value=MagicMock())
        conn.set_session_mode = AsyncMock()
        conn.set_session_model = AsyncMock()
        conn.authenticate = AsyncMock()
        conn.close = AsyncMock()
        return conn

    @staticmethod
    def _run_start(agent, state, *, conn):
        """Run the real _start_acp_server with transport mocked; return the env
        dict handed to the subprocess."""
        from contextlib import ExitStack

        from openhands.sdk.utils.async_executor import AsyncExecutor

        captured: dict[str, Any] = {}
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()

        async def _fake_exec(*_args, **kwargs):
            captured["env"] = kwargs.get("env")
            return mock_process

        async def _fake_filter(_src, _dst):
            return None

        agent._executor = AsyncExecutor()
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "openhands.sdk.agent.acp_agent.asyncio.create_subprocess_exec",
                    new=_fake_exec,
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
        return captured["env"]

    @staticmethod
    def _state(tmp_path, *, persisted: bool = True):
        from openhands.sdk.agent.acp_agent import ACPAgent

        agent = ACPAgent(acp_command=["codex-acp"])
        workspace = LocalWorkspace(working_dir=str(tmp_path / "ws"))
        (tmp_path / "ws").mkdir()
        persistence_dir = (
            str(tmp_path / "conversations" / uuid.uuid4().hex) if persisted else None
        )
        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=persistence_dir,
        )
        return state

    def test_codex_auth_json_materialises_to_conversation_root(self, tmp_path):
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()
        state = self._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        state.secret_registry.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr('{"tokens": "x"}'))}
        )
        env = self._run_start(agent, state, conn=self._make_conn())

        codex_home = Path(env["CODEX_HOME"])
        assert codex_home == Path(persist) / "acp" / "codex"
        auth_file = codex_home / "auth.json"
        assert auth_file.read_text(encoding="utf-8") == '{"tokens": "x"}'
        # 0600 file inside a 0700 dir, and the shared acp/ parent is 0700 too.
        assert auth_file.stat().st_mode & 0o777 == 0o600
        assert codex_home.stat().st_mode & 0o777 == 0o700
        assert codex_home.parent.stat().st_mode & 0o777 == 0o700
        # The blob is not exported as an env var.
        assert "CODEX_AUTH_JSON" not in env

    def test_gemini_vertex_sa_materialises_and_points_at_file(self, tmp_path):
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()
        state = self._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        state.secret_registry.update_secrets(
            {
                "GOOGLE_APPLICATION_CREDENTIALS_JSON": StaticSecret(
                    value=SecretStr('{"type": "service_account"}')
                )
            }
        )
        env = self._run_start(
            agent, state, conn=self._make_conn(agent_name="gemini-cli")
        )

        gac = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
        assert gac == Path(persist) / "acp" / "gemini-cli" / "gcloud-credentials.json"
        assert gac.read_text(encoding="utf-8") == '{"type": "service_account"}'
        # 0600 file inside a 0700 dir (symmetry with the Codex test).
        assert gac.stat().st_mode & 0o777 == 0o600
        assert gac.parent.stat().st_mode & 0o777 == 0o700
        assert "GOOGLE_APPLICATION_CREDENTIALS_JSON" not in env

    def test_seed_if_absent_does_not_clobber_existing_file(self, tmp_path):
        """A non-empty existing credential file (e.g. a token the CLI refreshed)
        is preserved; the stale pasted blob does not overwrite it."""
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()
        state = self._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        # Pre-seed a "refreshed" auth.json with deliberately wide (0644) perms.
        codex_home = Path(persist) / "acp" / "codex"
        codex_home.mkdir(parents=True)
        refreshed = codex_home / "auth.json"
        refreshed.write_text('{"refreshed": true}', encoding="utf-8")
        refreshed.chmod(0o644)

        state.secret_registry.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr('{"stale": true}'))}
        )
        env = self._run_start(agent, state, conn=self._make_conn())

        assert Path(env["CODEX_HOME"]) == codex_home
        # Contents preserved (not clobbered by the stale paste)...
        assert refreshed.read_text(encoding="utf-8") == '{"refreshed": true}'
        # ...but perms are still clamped to 0600 (regression: QA found a
        # preserved 0644 file staying world-readable).
        assert refreshed.stat().st_mode & 0o777 == 0o600

    def test_reads_reserved_secret_seeded_from_agent_context(self, tmp_path):
        """A reserved file secret supplied via agent_context.secrets (canvas-local
        path) is seeded into the registry at conversation init and materialised."""
        from openhands.sdk.conversation.impl.local_conversation import (
            LocalConversation,
        )
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                current_datetime=None,
                secrets={"CODEX_AUTH_JSON": StaticSecret(value=SecretStr('{"a": 1}'))},
            ),
        )
        conv = LocalConversation(
            agent,
            workspace=str(tmp_path / "ws"),
            persistence_dir=str(tmp_path / "conversations"),
        )
        try:
            env = self._run_start(agent, conv.state, conn=self._make_conn())
        finally:
            conv.close()

        auth_file = Path(env["CODEX_HOME"]) / "auth.json"
        assert auth_file.read_text(encoding="utf-8") == '{"a": 1}'
        assert "CODEX_AUTH_JSON" not in env

    def test_fallback_root_when_not_persisted(self, tmp_path):
        """With no persistence_dir, the file lands under the workspace tree —
        still seed-if-absent, no TemporaryDirectory."""
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()
        state = self._state(tmp_path, persisted=False)
        assert state.persistence_dir is None
        state.secret_registry.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("{}"))}
        )
        env = self._run_start(agent, state, conn=self._make_conn())

        expected = Path(state.workspace.working_dir) / ".openhands" / "acp" / "codex"
        assert Path(env["CODEX_HOME"]) == expected
        assert (expected / "auth.json").is_file()

    def test_no_file_secret_when_secret_absent(self, tmp_path):
        """No reserved secret present -> no data-dir env var is set."""
        agent = _make_agent()
        state = self._state(tmp_path)
        env = self._run_start(agent, state, conn=self._make_conn())
        assert "CODEX_HOME" not in env

    def test_materialisation_oserror_fails_fast(self, tmp_path):
        """If the credential can't be written (e.g. read-only mount), the error
        propagates out of _start_acp_server (so init_state surfaces a typed
        ConversationErrorEvent) instead of being swallowed and leaving the CLI
        to fail at auth time with no SDK breadcrumb."""
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()
        state = self._state(tmp_path)
        state.secret_registry.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("{}"))}
        )
        with patch(
            "openhands.sdk.agent.acp_agent._write_secret_file",
            side_effect=OSError("[Errno 30] Read-only file system"),
        ):
            with pytest.raises(OSError, match="Read-only file system"):
                self._run_start(agent, state, conn=self._make_conn())

    def test_vertex_warns_when_project_unset(self, tmp_path, caplog):
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()
        state = self._state(tmp_path)
        state.secret_registry.update_secrets(
            {"GOOGLE_APPLICATION_CREDENTIALS_JSON": StaticSecret(value=SecretStr("{}"))}
        )
        with caplog.at_level("WARNING"):
            self._run_start(agent, state, conn=self._make_conn(agent_name="gemini-cli"))
        assert any("GOOGLE_CLOUD_PROJECT" in rec.message for rec in caplog.records)

    def test_present_file_secret_names_helper(self, tmp_path):
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent()
        state = self._state(tmp_path)
        state.secret_registry.update_secrets(
            {
                "CODEX_AUTH_JSON": StaticSecret(value=SecretStr("{}")),
                "PLAIN_TOKEN": StaticSecret(value=SecretStr("t")),
            }
        )
        assert agent._present_file_secret_names(state) == {"CODEX_AUTH_JSON"}

    def test_blob_excluded_from_custom_secrets_advertisement(self, tmp_path):
        """The <CUSTOM_SECRETS> advertisement lists plain secrets but not the
        file-content blob (it's not an env var the agent can reference)."""
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(current_datetime=None),
        )
        state = self._state(tmp_path)
        state.secret_registry.update_secrets(
            {
                "CODEX_AUTH_JSON": StaticSecret(value=SecretStr("{}")),
                "PLAIN_TOKEN": StaticSecret(
                    value=SecretStr("t"), description="A plain token"
                ),
            }
        )
        events: list = []
        with patch("openhands.sdk.agent.acp_agent.ACPAgent._start_acp_server"):
            agent.init_state(state, on_event=events.append)

        suffix = events[0].dynamic_context
        assert suffix is not None
        assert "PLAIN_TOKEN" in suffix.text
        assert "CODEX_AUTH_JSON" not in suffix.text

    def test_downstream_can_override_specs_with_custom_provider(self, tmp_path):
        """A downstream app supplies its own ACPFileSecretSpec for a custom CLI;
        the SDK mechanism materialises it without any registry change."""
        from openhands.sdk import ACPFileSecretSpec
        from openhands.sdk.secret import StaticSecret

        custom = ACPFileSecretSpec(
            secret_name="MYCLI_TOKEN_JSON",
            filename="token.json",
            env_var="MYCLI_HOME",
            subdir="mycli",
            env_points_to="dir",
        )
        agent = _make_agent(acp_file_secrets=[custom])
        state = self._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        state.secret_registry.update_secrets(
            {"MYCLI_TOKEN_JSON": StaticSecret(value=SecretStr('{"t": 1}'))}
        )
        env = self._run_start(agent, state, conn=self._make_conn())

        home = Path(env["MYCLI_HOME"])
        assert home == Path(persist) / "acp" / "mycli"
        assert (home / "token.json").read_text(encoding="utf-8") == '{"t": 1}'
        assert "MYCLI_TOKEN_JSON" not in env
        # The built-in Codex/Gemini specs were replaced, so their secrets would
        # NOT be materialised by this agent.
        assert agent._present_file_secret_names(state) == {"MYCLI_TOKEN_JSON"}

    def test_empty_specs_disables_materialisation(self, tmp_path):
        """With acp_file_secrets=[], a CODEX_AUTH_JSON secret is treated as an
        ordinary env var (no file written, no CODEX_HOME) — downstream opt-out."""
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(acp_file_secrets=[])
        state = self._state(tmp_path)
        state.secret_registry.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("blob"))}
        )
        env = self._run_start(agent, state, conn=self._make_conn())

        assert "CODEX_HOME" not in env
        # Not configured as a file-secret, so it flows through as a plain env var.
        assert env.get("CODEX_AUTH_JSON") == "blob"

    def test_settings_pass_file_secrets_through_create_agent(self):
        """ACPAgentSettings defaults to the built-in specs and forwards them to
        the constructed ACPAgent."""
        from openhands.sdk.settings.acp_providers import default_acp_file_secrets
        from openhands.sdk.settings.model import ACPAgentSettings

        settings = ACPAgentSettings(acp_server="codex")
        agent = settings.create_agent()
        names = {s.secret_name for s in agent.acp_file_secrets}
        assert "CODEX_AUTH_JSON" in names
        assert {s.secret_name for s in agent.acp_file_secrets} == {
            s.secret_name for s in default_acp_file_secrets()
        }


# ---------------------------------------------------------------------------
# Per-conversation CLI data-dir isolation (issue #1019)
# ---------------------------------------------------------------------------


class TestACPDataDirIsolation:
    """``acp_isolate_data_dir`` relocates each provider's CLI data/config root to
    ``<persistence_dir>/acp/<provider>`` so conversations sharing one sandbox
    don't race on a shared HOME. Reuses the materialisation harness so the two
    features are exercised against the same _start_acp_server path.
    """

    _H = TestACPFileSecretMaterialisation

    @staticmethod
    def _agent(command, **kw):
        return ACPAgent(acp_command=command, acp_isolate_data_dir=True, **kw)

    def test_codex_sets_codex_home_to_conversation_root(self, tmp_path):
        agent = self._agent(["codex-acp"])
        state = self._H._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(agent, state, conn=self._H._make_conn())
        data_dir = Path(env["CODEX_HOME"])
        assert data_dir == Path(persist) / "acp" / "codex"
        assert data_dir.is_dir()
        assert data_dir.stat().st_mode & 0o777 == 0o700

    def test_gemini_sets_home_to_conversation_root(self, tmp_path):
        agent = self._agent(["npx", "-y", "@google/gemini-cli", "--acp"])
        state = self._H._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(
                agent, state, conn=self._H._make_conn(agent_name="gemini-cli")
            )
        assert Path(env["HOME"]) == Path(persist) / "acp" / "gemini-cli"

    def test_disabled_by_default_leaves_home_shared(self, tmp_path):
        # Same codex command, isolation OFF (the default): no CODEX_HOME injected.
        agent = ACPAgent(acp_command=["codex-acp"])
        state = self._H._state(tmp_path)
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(agent, state, conn=self._H._make_conn())
        assert "CODEX_HOME" not in env

    def test_unknown_command_no_ops(self, tmp_path):
        agent = self._agent(["my-custom-acp", "serve"])
        state = self._H._state(tmp_path)
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(agent, state, conn=self._H._make_conn())
        assert "CODEX_HOME" not in env
        assert "CLAUDE_CONFIG_DIR" not in env

    def test_falls_back_to_workspace_when_not_persisted(self, tmp_path):
        agent = self._agent(["codex-acp"])
        state = self._H._state(tmp_path, persisted=False)
        assert state.persistence_dir is None
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(agent, state, conn=self._H._make_conn())
        assert (
            Path(env["CODEX_HOME"])
            == Path(state.workspace.working_dir) / ".openhands" / "acp" / "codex"
        )

    def test_composes_with_materialised_codex_auth(self, tmp_path):
        """Isolation and file-secret materialisation agree on one CODEX_HOME."""
        from openhands.sdk.secret import StaticSecret

        agent = self._agent(["codex-acp"])
        state = self._H._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        state.secret_registry.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr('{"tokens": "x"}'))}
        )
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(agent, state, conn=self._H._make_conn())
        codex_home = Path(env["CODEX_HOME"])
        assert codex_home == Path(persist) / "acp" / "codex"
        # Materialisation seeded auth.json into the SAME dir isolation points at.
        assert (codex_home / "auth.json").read_text(
            encoding="utf-8"
        ) == '{"tokens": "x"}'

    # --- Claude: isolation applies under either auth mode (#3588) ------------

    def test_claude_isolates_under_api_key(self, tmp_path):
        from openhands.sdk.secret import StaticSecret

        agent = self._agent(["npx", "-y", "@agentclientprotocol/claude-agent-acp"])
        state = self._H._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        state.secret_registry.update_secrets(
            {"ANTHROPIC_API_KEY": StaticSecret(value=SecretStr("sk-live"))}
        )
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(
                agent, state, conn=self._H._make_conn(agent_name="claude-agent-acp")
            )
        # #3588: the conflict strip is keyed on CLAUDE_CODE_OAUTH_TOKEN, not on
        # CLAUDE_CONFIG_DIR, so relocating the data dir no longer strips a working
        # API key — API-key Claude gets the same per-conversation isolation.
        assert Path(env["CLAUDE_CONFIG_DIR"]) == Path(persist) / "acp" / "claude-code"
        assert env["ANTHROPIC_API_KEY"] == "sk-live"

    def test_claude_isolates_under_oauth_token(self, tmp_path):
        from openhands.sdk.secret import StaticSecret

        agent = self._agent(["npx", "-y", "@agentclientprotocol/claude-agent-acp"])
        state = self._H._state(tmp_path)
        persist = state.persistence_dir
        assert persist is not None
        state.secret_registry.update_secrets(
            {"CLAUDE_CODE_OAUTH_TOKEN": StaticSecret(value=SecretStr("oauth-xyz"))}
        )
        with patch.dict("os.environ", {}, clear=True):
            env = self._H._run_start(
                agent, state, conn=self._H._make_conn(agent_name="claude-agent-acp")
            )
        assert Path(env["CLAUDE_CONFIG_DIR"]) == Path(persist) / "acp" / "claude-code"


# ---------------------------------------------------------------------------
# Secret masking (#1023)
# ---------------------------------------------------------------------------


def _redacting_mask(text: str) -> str:
    """Stand-in for ``secret_registry.mask_secrets_in_output``: replaces the
    literal secret with the same sentinel the real registry uses."""
    return text.replace("SEKRET", "<secret-hidden>")


class TestMaskJsonValue:
    """Unit tests for the recursive JSON masker helper."""

    def test_masks_bare_string(self):
        assert _mask_json_value("token=SEKRET", _redacting_mask) == (
            "token=<secret-hidden>"
        )

    def test_masks_nested_dict_and_list(self):
        value = {
            "command": "curl -H 'Authorization: Bearer SEKRET'",
            "args": ["--data", "key=SEKRET"],
            "count": 3,
            "ok": True,
            "nothing": None,
        }
        masked = _mask_json_value(value, _redacting_mask)
        assert masked["command"] == "curl -H 'Authorization: Bearer <secret-hidden>'"
        assert masked["args"] == ["--data", "key=<secret-hidden>"]
        # Non-string leaves pass through unchanged.
        assert masked["count"] == 3
        assert masked["ok"] is True
        assert masked["nothing"] is None

    def test_non_string_scalar_passthrough(self):
        assert _mask_json_value(42, _redacting_mask) == 42
        assert _mask_json_value(None, _redacting_mask) is None


class TestACPBridgeMasking:
    """``_OpenHandsACPBridge`` masks injected secrets before they reach the
    ``on_token`` / ``on_event`` sinks (persisted + network-relayed)."""

    @pytest.mark.asyncio
    async def test_message_chunk_masked_in_relay_and_accumulation(self):
        from acp.schema import AgentMessageChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        client.mask = _redacting_mask
        tokens: list[str] = []
        client.on_token = tokens.append

        chunk = MagicMock(spec=AgentMessageChunk)
        chunk.content = MagicMock(spec=TextContentBlock)
        chunk.content.text = "the token is SEKRET"

        await client.session_update("sess-1", chunk)

        assert tokens == ["the token is <secret-hidden>"]
        assert client.accumulated_text == ["the token is <secret-hidden>"]

    @pytest.mark.asyncio
    async def test_thought_chunk_masked(self):
        from acp.schema import AgentThoughtChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        client.mask = _redacting_mask

        chunk = MagicMock(spec=AgentThoughtChunk)
        chunk.content = MagicMock(spec=TextContentBlock)
        chunk.content.text = "I will use SEKRET"

        await client.session_update("sess-1", chunk)

        assert client.accumulated_thoughts == ["I will use <secret-hidden>"]

    @pytest.mark.asyncio
    async def test_tool_call_start_masks_raw_fields(self):
        from acp.schema import ToolCallStart

        client = _OpenHandsACPBridge()
        client.mask = _redacting_mask
        events: list = []
        client.on_event = events.append

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Running: echo SEKRET"
        start.kind = "execute"
        start.status = "in_progress"
        start.raw_input = {"command": "echo SEKRET"}
        start.raw_output = "leaked SEKRET here"
        start.content = None

        await client.session_update("sess-1", start)

        assert len(events) == 1
        evt = events[0]
        assert evt.title == "Running: echo <secret-hidden>"
        assert evt.raw_input == {"command": "echo <secret-hidden>"}
        assert evt.raw_output == "leaked <secret-hidden> here"
        # The accumulator itself must hold masked values so the supersede /
        # flush path can't re-leak them.
        stored = client.accumulated_tool_calls[0]
        assert stored["title"] == "Running: echo <secret-hidden>"
        assert stored["raw_input"] == {"command": "echo <secret-hidden>"}
        assert stored["raw_output"] == "leaked <secret-hidden> here"

    @pytest.mark.asyncio
    async def test_tool_call_progress_masks_terminal_output(self):
        from acp.schema import ToolCallProgress, ToolCallStart

        client = _OpenHandsACPBridge()
        client.mask = _redacting_mask
        events: list = []
        client.on_event = events.append

        start = MagicMock(spec=ToolCallStart)
        start.tool_call_id = "tc-1"
        start.title = "Run"
        start.kind = "execute"
        start.status = "in_progress"
        start.raw_input = None
        start.raw_output = None
        start.content = None
        await client.session_update("sess-1", start)

        # Terminal progress frame carries the secret in its cumulative output.
        progress = MagicMock(spec=ToolCallProgress)
        progress.tool_call_id = "tc-1"
        progress.title = None
        progress.kind = None
        progress.status = "completed"
        progress.raw_input = None
        progress.raw_output = "result: SEKRET"
        progress.content = None
        await client.session_update("sess-1", progress)

        # The terminal event (emitted on the in_progress->completed transition)
        # carries masked output.
        assert events[-1].status == "completed"
        assert events[-1].raw_output == "result: <secret-hidden>"
        assert (
            client.accumulated_tool_calls[0]["raw_output"] == "result: <secret-hidden>"
        )

    @pytest.mark.asyncio
    async def test_no_masking_when_mask_unset(self):
        """A standalone bridge (mask is None) passes text through unchanged
        and never raises."""
        from acp.schema import AgentMessageChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        assert client.mask is None
        tokens: list[str] = []
        client.on_token = tokens.append

        chunk = MagicMock(spec=AgentMessageChunk)
        chunk.content = MagicMock(spec=TextContentBlock)
        chunk.content.text = "raw SEKRET"

        await client.session_update("sess-1", chunk)

        assert tokens == ["raw SEKRET"]
        assert client.accumulated_text == ["raw SEKRET"]

    def test_mask_value_swallows_mask_errors(self):
        """A failing masker must never crash session_update — fall back to the
        original value (matches the regular terminal tool's masking contract)."""

        def _boom(_text: str) -> str:
            raise RuntimeError("masker exploded")

        client = _OpenHandsACPBridge()
        client.mask = _boom
        assert client._mask_value("keep SEKRET") == "keep SEKRET"

    def test_reset_preserves_mask(self):
        """mask is conversation-lifetime (bound once in _start_acp_server), so a
        per-turn reset() must NOT clear it — unlike on_token/on_event."""
        client = _OpenHandsACPBridge()
        client.mask = _redacting_mask
        client.reset()
        assert client.mask is _redacting_mask

    @pytest.mark.asyncio
    async def test_fork_session_text_masked(self):
        """ask_agent() joins _fork_accumulated_text and returns it to the
        caller, so fork-session chunks must be masked too."""
        from acp.schema import AgentMessageChunk, TextContentBlock

        client = _OpenHandsACPBridge()
        client.mask = _redacting_mask
        client._fork_session_id = "fork-1"

        chunk = MagicMock(spec=AgentMessageChunk)
        chunk.content = MagicMock(spec=TextContentBlock)
        chunk.content.text = "fork says SEKRET"

        await client.session_update("fork-1", chunk)

        assert client._fork_accumulated_text == ["fork says <secret-hidden>"]


class TestACPStepMasksPersistedTurn:
    """End-to-end: the persisted FinishAction text is masked at the join
    boundary, including secrets split across streamed chunks."""

    def _make_conversation_with_message(self, tmp_path, text="Hello"):
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

    def test_finish_action_masks_secret_split_across_chunks(self, tmp_path):
        agent = _make_agent()
        conversation = self._make_conversation_with_message(tmp_path)
        # Seed the mask set via the canonical registry path (get_secret_value
        # records the resolved value in _exported_values) — the same path
        # _start_acp_server drives for StartConversationRequest secrets.
        reg = conversation.state.secret_registry
        reg.update_secrets({"TOKEN": "supersecret"})
        reg.get_secret_value("TOKEN")
        events: list = []

        mock_client = _OpenHandsACPBridge()
        agent._client = mock_client
        agent._conn = MagicMock()
        agent._session_id = "test-session"

        def _fake_run_async(_coro, **_kwargs):
            # Populate accumulated_text directly, bypassing session_update (and
            # thus per-chunk masking) on purpose: this isolates the join-boundary
            # re-mask in _finalize_successful_turn. The secret straddles two
            # chunks, so neither chunk matches alone — only the reassembled join
            # does, which is exactly what the persistence-boundary mask catches.
            mock_client.accumulated_text.append("the value is super")
            mock_client.accumulated_text.append("secret now")

        mock_executor = MagicMock()
        mock_executor.run_async = _fake_run_async
        agent._executor = mock_executor

        agent.step(conversation, on_event=events.append)

        finish = next(
            e for e in events if isinstance(getattr(e, "action", None), FinishAction)
        )
        assert "supersecret" not in finish.action.message
        assert finish.action.message == "the value is <secret-hidden> now"
