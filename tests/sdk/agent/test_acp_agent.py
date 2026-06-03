"""Tests for ACPAgent."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from base64 import urlsafe_b64encode
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp.exceptions import RequestError as ACPRequestError
from acp.schema import PromptResponse
from pydantic import SecretStr

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    _classify_acp_init_error,
    _codex_auth_file,
    _estimate_cost_from_tokens,
    _extract_session_models,
    _extract_token_usage,
    _image_url_to_acp_block,
    _maybe_set_session_model,
    _OpenHandsACPBridge,
    _reapply_session_model_on_resume,
    _select_auth_method,
    _serialize_tool_content,
)
from openhands.sdk.agent.acp_models import ACPModelInfo
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context import AgentContext
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
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE
from openhands.sdk.workspace.local import LocalWorkspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(**kwargs) -> ACPAgent:
    return ACPAgent(acp_command=["echo", "test"], **kwargs)


def _make_cipher() -> Cipher:
    """Deterministic Fernet cipher for round-trip tests."""
    return Cipher(urlsafe_b64encode(b"a" * 32).decode("ascii"))


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
        # ``acp_env`` is redacted by default, so a value-preserving round-trip
        # requires expose_secrets=True (same contract as ``LLM.api_key``).
        dumped = agent.model_dump_json(context={"expose_secrets": True})
        restored = AgentBase.model_validate_json(dumped)
        assert isinstance(restored, ACPAgent)
        assert restored.acp_command == agent.acp_command
        assert restored.acp_args == agent.acp_args
        assert restored.acp_env == agent.acp_env

    def test_acp_env_redacted_by_default(self):
        """``acp_env`` values must be masked in default serialization output.

        Regression guard: trace dumps consumed by evaluation tooling embed the
        full ACPAgent state under ``history[*].value.agent``. Before masking,
        live proxy keys leaked into shareable archives.
        """
        agent = ACPAgent(
            acp_command=["echo", "test"],
            acp_env={
                "OPENAI_API_KEY": "sk-real-secret-do-not-leak",
                "GEMINI_API_KEY": "sk-other-secret",
                "GEMINI_BASE_URL": "https://llm-proxy.example/",
            },
        )

        # In-memory state still holds the real values — only serialization masks.
        assert agent.acp_env["OPENAI_API_KEY"] == "sk-real-secret-do-not-leak"

        # model_dump returns SecretStr objects — real values are hidden.
        dumped = agent.model_dump()
        for v in dumped["acp_env"].values():
            assert str(v) == REDACTED_SECRET_VALUE

        # JSON path that produced the original leaks must not contain any of
        # the real values.
        dumped_json = agent.model_dump_json()
        assert "sk-real-secret-do-not-leak" not in dumped_json
        assert "sk-other-secret" not in dumped_json
        assert "https://llm-proxy.example/" not in dumped_json
        assert REDACTED_SECRET_VALUE in dumped_json

    def test_acp_env_exposed_with_expose_secrets(self):
        """``expose_secrets=True`` returns the real values for transport use."""
        secrets = {
            "OPENAI_API_KEY": "sk-real-secret",
            "BASE_URL": "https://llm-proxy.example/",
        }
        agent = ACPAgent(acp_command=["echo", "test"], acp_env=dict(secrets))

        dumped = agent.model_dump(context={"expose_secrets": True})
        assert dumped["acp_env"] == secrets

        # Round-trip with expose_secrets must reconstruct the original values.
        json_blob = agent.model_dump_json(context={"expose_secrets": True})
        restored = AgentBase.model_validate_json(json_blob)
        assert isinstance(restored, ACPAgent)
        assert restored.acp_env == secrets

    def test_acp_env_serializer_does_not_mutate_in_memory_state(self):
        """Serialization must not mutate ``self.acp_env`` — the runtime path
        (:meth:`ACPAgent._start_acp_server`) reads it directly to populate the
        subprocess environment.
        """
        original = {"OPENAI_API_KEY": "sk-real-secret"}
        agent = ACPAgent(acp_command=["echo", "test"], acp_env=dict(original))

        # Multiple dumps in different modes must leave the live dict alone.
        agent.model_dump()
        agent.model_dump_json()
        agent.model_dump(context={"expose_secrets": True})

        assert agent.acp_env == original

    def test_deserialization_from_dict(self):
        data = {
            "kind": "ACPAgent",
            "acp_command": ["echo", "test"],
        }
        agent = AgentBase.model_validate(data)
        assert isinstance(agent, ACPAgent)
        assert agent.acp_command == ["echo", "test"]

    def test_acp_env_decrypts_ciphertext_with_cipher_in_context(self):
        """Round-trip Fernet-encrypted ``acp_env`` values via cipher context.

        Regression for a real production bug in v1.24.0: the on-disk →
        ACPAgentSettings → ACPAgent path could leave Fernet ciphertext as
        the field value because only the settings-side variant had a
        decryption ``field_validator``. The conversation-start flow
        validates the full :class:`StoredConversation` with cipher
        context after the agent was already constructed (without cipher)
        from ``StartConversationRequest.agent_settings`` — and without
        the validator here, the ciphertext survives that re-validation
        and reaches the ACP subprocess as the env-var value. The
        provider call then fails (e.g. Anthropic reads the Fernet token
        as ``ANTHROPIC_BASE_URL`` and 400s on URL parsing).
        """
        cipher = _make_cipher()
        encrypted_key = cipher.encrypt(SecretStr("sk-real"))
        encrypted_url = cipher.encrypt(SecretStr("https://api.example.com"))
        assert encrypted_key is not None
        assert encrypted_url is not None

        # Build the wire payload an agent-server would receive: an
        # ACPAgent dict whose ``acp_env`` values are Fernet ciphertext.
        data = {
            "kind": "ACPAgent",
            "acp_command": ["echo", "test"],
            "acp_env": {
                "ANTHROPIC_API_KEY": encrypted_key,
                "ANTHROPIC_BASE_URL": encrypted_url,
            },
        }

        restored = AgentBase.model_validate(data, context={"cipher": cipher})
        assert isinstance(restored, ACPAgent)
        assert restored.acp_env == {
            "ANTHROPIC_API_KEY": "sk-real",
            "ANTHROPIC_BASE_URL": "https://api.example.com",
        }

    def test_acp_env_no_cipher_in_context_leaves_ciphertext_untouched(self):
        """The ``cipher is None`` branch of the validator is exercised on
        every code path that round-trips an agent dict without supplying
        a cipher (e.g. test serialization helpers, JSON-only diagnostic
        dumps). In that mode the ciphertext must survive verbatim — both
        because there's nothing to decrypt with, and because mutating it
        would defeat a downstream caller that *will* validate again with
        the cipher present (the conversation-start re-validation step).
        """
        cipher = _make_cipher()
        encrypted = cipher.encrypt(SecretStr("sk-real"))
        assert encrypted is not None

        data = {
            "kind": "ACPAgent",
            "acp_command": ["echo", "test"],
            "acp_env": {"ANTHROPIC_API_KEY": encrypted},
        }
        restored = AgentBase.model_validate(data)
        assert isinstance(restored, ACPAgent)
        assert restored.acp_env == {"ANTHROPIC_API_KEY": encrypted}

    def test_acp_env_plaintext_passes_through_with_cipher(self):
        """First writes from clients that never went through the encryption
        pipeline carry plaintext. They must still validate cleanly when the
        server happens to have a cipher in context."""
        cipher = _make_cipher()
        data = {
            "kind": "ACPAgent",
            "acp_command": ["echo", "test"],
            "acp_env": {"FOO": "plaintext-value"},
        }
        restored = AgentBase.model_validate(data, context={"cipher": cipher})
        assert isinstance(restored, ACPAgent)
        assert restored.acp_env == {"FOO": "plaintext-value"}

    def test_acp_env_undecryptable_ciphertext_passes_through_with_warning(self, caplog):
        """Cipher mismatch / corruption shouldn't crash agent construction.

        Mirrors the MCP env/header pattern: a ciphertext we can't decrypt
        is left in place with a logged warning so the operator can repair
        it, rather than turning into a hard failure that bricks the
        agent.
        """
        cipher = _make_cipher()
        # Looks like a Fernet token (prefix matches) but isn't a valid
        # one — try_decrypt_str returns None.
        bogus = "gAAAAA" + ("x" * 80)
        data = {
            "kind": "ACPAgent",
            "acp_command": ["echo", "test"],
            "acp_env": {"BUSTED": bogus},
        }
        with caplog.at_level("WARNING"):
            restored = AgentBase.model_validate(data, context={"cipher": cipher})
        assert isinstance(restored, ACPAgent)
        assert restored.acp_env == {"BUSTED": bogus}
        assert any("could not be decrypted" in r.message for r in caplog.records)


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

    def test_astep_times_out_while_tool_call_is_inflight(self, tmp_path):
        """A hard ACP prompt timeout still fires during an active tool call.

        Mirroring OpenHands command handling, active output/heartbeats keep the
        runtime alive but do not let a never-ending command suppress the hard
        turn deadline. The timeout path must cancel the ACP session and close
        any streamed tool cards as failed.
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
        (auth_dir / "auth.json").write_text("{}", encoding="utf-8")

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
        (codex_home / "auth.json").write_text("{}", encoding="utf-8")
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
# ACP model overrides
# ---------------------------------------------------------------------------


class TestMaybeSetSessionModel:
    @pytest.mark.asyncio
    async def test_codex_agent_uses_protocol_model_override(self):
        conn = AsyncMock()
        applied = await _maybe_set_session_model(
            conn, "codex-acp", "session-1", "gpt-5.4"
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="gpt-5.4",
            session_id="session-1",
        )
        # The override was actually pushed to the server via the protocol call.
        assert applied is True

    @pytest.mark.asyncio
    async def test_meta_key_provider_skips_protocol_override_at_init(self):
        # claude-agent-acp selects its *initial* model via session _meta, so the
        # one-shot init set_session_model call is skipped (even though the
        # provider now supports the protocol call for runtime switches).
        conn = AsyncMock()
        applied = await _maybe_set_session_model(
            conn,
            "claude-agent-acp",
            "session-1",
            "claude-opus-4-6",
        )
        conn.set_session_model.assert_not_called()
        # Not applied *via this call* — claude rode the model in via _meta on
        # new_session, which the caller accounts for separately.
        assert applied is False

    @pytest.mark.asyncio
    async def test_missing_model_skips_protocol_override(self):
        conn = AsyncMock()
        applied = await _maybe_set_session_model(conn, "codex-acp", "session-1", None)
        conn.set_session_model.assert_not_called()
        assert applied is False

    @pytest.mark.asyncio
    async def test_unknown_provider_does_not_apply_override_at_init(self):
        # An unknown/custom server gets neither _meta nor the protocol call on a
        # fresh session, so the override never reaches the server.
        conn = AsyncMock()
        applied = await _maybe_set_session_model(
            conn, "some-custom-acp", "session-1", "whatever"
        )
        conn.set_session_model.assert_not_called()
        assert applied is False


class TestReapplySessionModelOnResume:
    """Resume reapplies the persisted model via the runtime-switch gate."""

    @pytest.mark.asyncio
    async def test_claude_reapplies_persisted_model_on_resume(self):
        # claude selects its initial model via _meta (supports_set_session_model
        # =False) but DOES support set_session_model for runtime switches.
        # load_session() carries no _meta, so on resume the persisted model must
        # be reapplied via the runtime-switch gate — _maybe_set_session_model
        # would skip it.
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "claude-agent-acp", "sess-1", "claude-haiku-4-5-20251001"
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="claude-haiku-4-5-20251001", session_id="sess-1"
        )
        assert applied is True

    @pytest.mark.asyncio
    async def test_codex_reapplies_persisted_model_on_resume(self):
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "codex-acp", "sess-1", "gpt-5.4/low"
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="gpt-5.4/low", session_id="sess-1"
        )
        assert applied is True

    @pytest.mark.asyncio
    async def test_missing_model_skips_reapply(self):
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "claude-agent-acp", "sess-1", None
        )
        conn.set_session_model.assert_not_called()
        assert applied is False

    @pytest.mark.asyncio
    async def test_unknown_provider_attempts_reapply(self):
        # provider=None (custom server) is allowed to attempt the switch by
        # set_acp_model, and such switches are persisted as authoritative — so
        # resume must mirror that and attempt the reapply too (otherwise the
        # resumed session would silently revert to the server default).
        conn = AsyncMock()
        applied = await _reapply_session_model_on_resume(
            conn, "some-custom-acp", "sess-1", "whatever"
        )
        conn.set_session_model.assert_awaited_once_with(
            model_id="whatever", session_id="sess-1"
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
                conn, "legacy-acp", "sess-1", "x"
            )
        conn.set_session_model.assert_not_called()
        assert applied is False

    @pytest.mark.asyncio
    async def test_client_rejection_is_swallowed_on_resume(self):
        # A client/protocol rejection (method-not-found = server doesn't support
        # the call, or invalid model id) must not break resume — mirrors the
        # load_session fallback. The error is logged, not raised.
        conn = AsyncMock()
        conn.set_session_model.side_effect = ACPRequestError(
            code=-32601, message="method not found"
        )
        applied = await _reapply_session_model_on_resume(
            conn, "some-custom-acp", "sess-1", "whatever"
        )
        conn.set_session_model.assert_awaited_once()
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
            conn, "codex-acp", "sess-1", "gpt-5.4/low"
        )
        conn.set_session_model.assert_awaited_once()
        assert applied is False


class TestSetACPModel:
    """Runtime (mid-conversation) model switching via set_session_model."""

    @staticmethod
    def _wire(agent: ACPAgent, agent_name: str) -> ACPAgent:
        agent._conn = MagicMock()
        agent._session_id = "sess-1"
        agent._agent_name = agent_name
        executor = MagicMock()
        executor.run_async = MagicMock()
        agent._executor = executor
        return agent

    def test_switches_model_on_live_codex_session(self):
        agent = self._wire(_make_agent(), "codex-acp")
        agent.set_acp_model("gpt-5.4/low")
        agent._conn.set_session_model.assert_called_once_with(
            model_id="gpt-5.4/low", session_id="sess-1"
        )
        agent._executor.run_async.assert_called_once()
        # Sentinel LLM + metrics reflect the live model for cost/token tracking.
        assert agent.llm.model == "gpt-5.4/low"
        assert agent.llm.metrics.model_name == "gpt-5.4/low"

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
        agent = self._wire(_make_agent(), "codex-acp")
        agent._executor.run_async.side_effect = ACPRequestError(
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
        agent._executor.run_async.side_effect = ACPRequestError(
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
        agent.set_acp_model("gpt-5.4/low")
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

    def test_unknown_provider_surfaces_server_model_not_unapplied_override(
        self, tmp_path
    ):
        """Fresh session on an unknown/custom provider with ``acp_model`` set:
        the override is never pushed to the server (no ``_meta``, no protocol
        call), so ``current_model_id`` must reflect what the server reported —
        not the override the live session isn't actually running.
        """
        agent = _make_agent(acp_model="caller-model")
        state = _make_state(tmp_path)
        new_response = MagicMock()
        new_response.session_id = "fresh-sess"
        new_response.models = self._models_block("server-model", ["server-model"])
        conn = self._make_conn()
        conn.initialize.return_value.agent_info.name = "some-custom-acp"
        conn.initialize.return_value.auth_methods = []
        conn.new_session = AsyncMock(return_value=new_response)

        self._patched_start_acp_server(agent, state, conn=conn)

        # Unknown provider => the override never reached the server.
        conn.set_session_model.assert_not_awaited()
        assert agent.current_model_id == "server-model"

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
    env so the ACP server (Claude Code, Codex CLI, etc.) can use them. This
    drain is the only thing that delivers ``agent_context.secrets`` to the CLI
    on paths that don't call ``create_request`` (e.g. canvas-local, which folds
    ``llm.api_key`` into ``agent_context.secrets`` server-side via
    ``create_agent`` but never lifts it into ``state.secret_registry``).
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
            # Hermetic: exclude the runner's ambient env (e.g. a real
            # GITHUB_TOKEN / ANTHROPIC_API_KEY) so it can't shadow the
            # registry/drain values under test — env.update(os.environ) runs
            # before the fill-if-absent secret tiers in _start_acp_server.
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
        """A StaticSecret in agent_context.secrets lands in the subprocess env.

        This drain is what delivers ``agent_context.secrets`` to the CLI on
        paths that don't lift them into ``state.secret_registry`` via
        ``create_request`` (e.g. canvas-local).
        """
        from pydantic import SecretStr

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
        env = self._run_start_capturing_env(agent, tmp_path)
        assert env.get("GITHUB_TOKEN") == "ghp_test123"

    def test_acp_env_takes_precedence_over_agent_context_secret(self, tmp_path):
        """An explicit acp_env entry wins over the same key in agent_context.secrets."""
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            acp_env={"MY_TOKEN": "acp-env-wins"},
            agent_context=AgentContext(
                secrets={"MY_TOKEN": StaticSecret(value=SecretStr("secret-panel"))}
            ),
        )
        with pytest.warns(DeprecationWarning, match=r"ACPAgent\.acp_env"):
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

    def test_acp_env_still_injected(self, tmp_path):
        """``acp_env`` (user arbitrary env vars) is still injected at spawn."""
        agent = _make_agent(acp_env={"MY_TOKEN": "acp-env-value"})
        with pytest.warns(DeprecationWarning, match=r"ACPAgent\.acp_env"):
            env = self._run_start_capturing_env(agent, tmp_path)
        assert env.get("MY_TOKEN") == "acp-env-value"

    def test_empty_acp_env_does_not_warn(self, tmp_path):
        """An empty ``acp_env`` must not emit the deprecation warning."""
        import warnings

        agent = _make_agent()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._run_start_capturing_env(agent, tmp_path)
        assert not [w for w in caught if "acp_env" in str(w.message)]


class TestACPSecretRegistryEnvInjection:
    """Tests for secret injection from the conversation's secret_registry.

    Secrets registered via ``Conversation.update_secrets()`` — or the
    equivalent ``payload.secrets`` channel that app-server callers
    (agent-canvas, the OpenHands cloud app server) use — must land in the
    ACP subprocess env without each caller having to also build an
    ``AgentContext(secrets=...)`` shim around the same data.

    Same-key precedence is
    ``acp_env > secret_registry > agent_context.secrets > os.environ``.
    Conversation secrets (registry + the agent_context drain) override ambient
    ``os.environ`` so an explicit per-conversation/provider secret wins over a
    same-named server env var; the registry wins over the drain on collision.
    """

    @staticmethod
    def _run_start_capturing_env(
        agent, tmp_path, *, registry_secrets=None, extra_os_env=None
    ) -> dict:
        """Re-uses the env-capture harness from TestACPSecretsEnvInjection."""
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
        from openhands.sdk.secret import SecretSource

        class _FakeLookupSecret(SecretSource):
            stored_value: str

            def get_value(self) -> str | None:
                return self.stored_value

        agent = _make_agent()
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={
                "OPENAI_API_KEY": _FakeLookupSecret(stored_value="sk-fake-openai")
            },
        )
        assert env.get("OPENAI_API_KEY") == "sk-fake-openai"

    def test_acp_env_takes_precedence_over_registry_secret(self, tmp_path):
        """An explicit ``acp_env`` entry wins over the same key in the registry."""
        agent = _make_agent(acp_env={"GITHUB_TOKEN": "from-acp-env"})
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"GITHUB_TOKEN": "from-registry"},
        )
        assert env.get("GITHUB_TOKEN") == "from-acp-env"

    def test_acp_env_shadow_skips_registry_lookup(self, tmp_path):
        """``acp_env`` shadowing a key must not trigger ``get_value()``.

        LookupSecret performs an HTTP request in production; calling it for
        a key that ``acp_env`` is about to override wastes a round-trip and
        can emit spurious lookup-failure warnings.
        """
        from pydantic import Field

        from openhands.sdk.secret import SecretSource

        class _CountingLookupSecret(SecretSource):
            stored_value: str
            calls: list[int] = Field(default_factory=list)

            def get_value(self) -> str | None:
                self.calls.append(1)
                return self.stored_value

        secret = _CountingLookupSecret(stored_value="from-registry")
        agent = _make_agent(acp_env={"GITHUB_TOKEN": "from-acp-env"})
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"GITHUB_TOKEN": secret},
        )
        assert env.get("GITHUB_TOKEN") == "from-acp-env"
        assert secret.calls == []

    def test_registry_secret_takes_precedence_over_agent_context_secret(self, tmp_path):
        """``secret_registry`` wins over ``agent_context.secrets`` on collision.

        Precedence: ``acp_env > secret_registry > agent_context.secrets >
        os.environ``. The registry is the canonical conversation-secret channel
        (``StartConversationRequest.secrets`` lands here). On a key collision the
        registry value is used (the drain skips keys the registry will set); a
        context-only key is still injected by the drain — proving the two
        channels coexist without double-injecting.
        """
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={
                    "GITHUB_TOKEN": StaticSecret(value=SecretStr("from-context")),
                    "CONTEXT_ONLY": StaticSecret(value=SecretStr("ctx-value")),
                }
            )
        )
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={"GITHUB_TOKEN": "from-registry"},
        )
        assert env.get("GITHUB_TOKEN") == "from-registry"
        assert env.get("CONTEXT_ONLY") == "ctx-value"

    def test_empty_registry_does_not_change_behaviour(self, tmp_path):
        """An empty secret_registry must not raise or alter the spawn env."""
        agent = _make_agent(acp_env={"FOO": "bar"})
        env = self._run_start_capturing_env(agent, tmp_path, registry_secrets=None)
        assert env.get("FOO") == "bar"

    def test_failing_registry_lookup_swallowed(self, tmp_path):
        """A secret source that raises is dropped, not propagated.

        ``SecretRegistry.get_secret_value`` already catches lookup
        errors and returns ``None``; the spawn-env loop must treat
        that ``None`` as "skip", so a transient secret-source failure
        (network blip, expired token) doesn't take the whole ACP
        subprocess down.
        """
        from openhands.sdk.secret import SecretSource

        class _BrokenSecret(SecretSource):
            def get_value(self) -> str | None:
                raise OSError("network down")

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
        """An agent_context.secrets drain value overrides ambient os.environ."""
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                secrets={"GITHUB_TOKEN": StaticSecret(value=SecretStr("from-context"))}
            )
        )
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            extra_os_env={"GITHUB_TOKEN": "ambient-should-lose"},
        )
        assert env.get("GITHUB_TOKEN") == "from-context"


class TestACPEnvConflictSuppression:
    """CLAUDE_CONFIG_DIR OAuth auth must not coexist with API-key env vars.

    When CLAUDE_CONFIG_DIR is present in the subprocess environment the agent
    uses a credential file for OAuth.  If ANTHROPIC_API_KEY or
    ANTHROPIC_BASE_URL are also present they redirect requests to a proxy that
    does not support OAuth bearer tokens, breaking auth silently.

    _start_acp_server must strip the conflicting vars regardless of where they
    came from: acp_env, os.environ, secret_registry, or agent_context.secrets.
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

    def test_claude_config_dir_suppresses_api_key_from_acp_env(self, tmp_path):
        """ANTHROPIC_API_KEY from acp_env is stripped when CLAUDE_CONFIG_DIR present."""
        agent = _make_agent(
            acp_env={
                "CLAUDE_CONFIG_DIR": "/tmp/claude-creds",
                "ANTHROPIC_API_KEY": "sk-conflict",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            }
        )
        env = self._run_start_capturing_env(agent, tmp_path)

        assert env["CLAUDE_CONFIG_DIR"] == "/tmp/claude-creds"
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_claude_config_dir_suppresses_api_key_from_os_environ(self, tmp_path):
        """ANTHROPIC_API_KEY leaking in from os.environ is stripped too."""
        agent = _make_agent(
            acp_env={"CLAUDE_CONFIG_DIR": "/tmp/claude-creds"},
        )
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            extra_os_env={
                "ANTHROPIC_API_KEY": "sk-leaked",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            },
        )

        assert "CLAUDE_CONFIG_DIR" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_claude_config_dir_suppresses_api_key_from_registry(self, tmp_path):
        """ANTHROPIC_API_KEY injected via secret_registry is stripped too.

        This is the channel provider creds now travel on (folded into
        ``agent_context.secrets`` by ``create_agent`` → lifted into the
        registry by ``create_request``).
        """
        agent = _make_agent(
            acp_env={"CLAUDE_CONFIG_DIR": "/tmp/claude-creds"},
        )
        env = self._run_start_capturing_env(
            agent,
            tmp_path,
            registry_secrets={
                "ANTHROPIC_API_KEY": "sk-from-registry",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            },
        )

        assert "CLAUDE_CONFIG_DIR" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_claude_config_dir_suppresses_api_key_from_secrets(self, tmp_path):
        """ANTHROPIC_API_KEY drained from agent_context.secrets is stripped too.

        Covers the canvas-local channel: provider creds folded into
        ``agent_context.secrets`` reach env via the drain, and must still be
        stripped when CLAUDE_CONFIG_DIR is active.
        """
        from pydantic import SecretStr

        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            acp_env={"CLAUDE_CONFIG_DIR": "/tmp/claude-creds"},
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
        with pytest.warns(DeprecationWarning, match=r"ACPAgent\.acp_env"):
            env = self._run_start_capturing_env(agent, tmp_path)

        assert "CLAUDE_CONFIG_DIR" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_no_suppression_without_claude_config_dir(self, tmp_path):
        """Without CLAUDE_CONFIG_DIR, ANTHROPIC_API_KEY passes through unchanged."""
        agent = _make_agent(
            acp_env={"ANTHROPIC_API_KEY": "sk-valid"},
        )
        env = self._run_start_capturing_env(agent, tmp_path)

        assert env.get("ANTHROPIC_API_KEY") == "sk-valid"
        assert "CLAUDE_CONFIG_DIR" not in env


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
        cur, avail = _extract_session_models(response)
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
        cur, avail = _extract_session_models(response)
        assert cur is None
        assert avail is None

    def test_returns_empty_list_when_available_models_missing(self):
        # ``models`` block present but ``availableModels`` absent/None: the
        # block WAS reported, so we return ``[]`` (present, no models), not None.
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = "gpt-5"
        response.models.available_models = None
        cur, avail = _extract_session_models(response)
        assert cur == "gpt-5"
        assert avail == []

    def test_returns_none_list_when_response_is_none(self):
        # ``load_session`` can return ``None`` for servers that don't
        # implement the call — the helper must not crash, and reports "absent".
        assert _extract_session_models(None) == (None, None)

    def test_returns_none_list_when_models_field_is_none(self):
        response = MagicMock()
        response.models = None
        assert _extract_session_models(response) == (None, None)

    def test_returns_none_when_current_model_id_is_empty_string(self):
        # An empty string is treated the same as a missing field — we don't
        # want to surface "" as a real model name. The block is present, so
        # available_models is [] (not None).
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = ""
        response.models.available_models = []
        assert _extract_session_models(response) == (None, [])

    def test_returns_none_when_current_model_id_is_not_a_string(self):
        # Defensive: an agent returning a non-string here is malformed.
        response = MagicMock()
        response.models = MagicMock()
        response.models.current_model_id = 42
        response.models.available_models = []
        assert _extract_session_models(response) == (None, [])


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
        response.models.current_model_id = "gpt-5.4/low"
        response.models.available_models = [
            self._raw("gpt-5.4/low", "gpt-5.4 (low)", "Strong everyday model."),
        ]
        _cur, avail = _extract_session_models(response)
        assert avail == [
            ACPModelInfo(
                model_id="gpt-5.4/low",
                name="gpt-5.4 (low)",
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
        _cur, avail = _extract_session_models(response)
        assert avail == [ACPModelInfo(model_id="good", name="Good", description=None)]


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
    """``supports_runtime_model_switch`` mirrors ``set_acp_model``'s gate.

    It refuses only for a *known* provider that declares no support, attempts
    optimistically for unknown/custom servers, and is ``False`` before a
    session exists.
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

    def test_optimistic_true_for_unknown_provider(self):
        # Mirrors set_acp_model, which attempts the call for unknown/custom
        # servers rather than refusing — so the picker isn't needlessly hidden.
        agent = _make_agent()
        agent._session_id = "sess-1"
        agent._agent_name = "some-third-party-acp-server"
        assert agent.supports_runtime_model_switch is True

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
        # 0600 file inside a 0700 dir.
        assert auth_file.stat().st_mode & 0o777 == 0o600
        assert codex_home.stat().st_mode & 0o777 == 0o700
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

    def test_reads_from_agent_context_secrets_drain(self, tmp_path):
        """The reserved secret can arrive via agent_context.secrets (canvas-local
        path) rather than the registry."""
        from openhands.sdk.secret import StaticSecret

        agent = _make_agent(
            agent_context=AgentContext(
                current_datetime=None,
                secrets={"CODEX_AUTH_JSON": StaticSecret(value=SecretStr('{"a": 1}'))},
            ),
        )
        state = self._state(tmp_path)
        env = self._run_start(agent, state, conn=self._make_conn())

        auth_file = Path(env["CODEX_HOME"]) / "auth.json"
        assert auth_file.read_text(encoding="utf-8") == '{"a": 1}'
        assert "CODEX_AUTH_JSON" not in env

    def test_acp_env_pin_wins_and_credential_seeds_where_it_points(self, tmp_path):
        """An explicit acp_env[CODEX_HOME] keeps its precedence, and the
        credential is seeded *there* so the file and env stay consistent."""
        from openhands.sdk.secret import StaticSecret

        pinned = tmp_path / "pinned_codex"
        # Pre-create the pinned dir with deliberately wide (0755) perms.
        pinned.mkdir()
        pinned.chmod(0o755)
        agent = _make_agent(acp_env={"CODEX_HOME": str(pinned)})
        state = self._state(tmp_path)
        state.secret_registry.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr('{"k": 1}'))}
        )
        env = self._run_start(agent, state, conn=self._make_conn())

        assert env["CODEX_HOME"] == str(pinned)
        # The credential lands under the pinned dir, not the conversation root.
        assert (pinned / "auth.json").read_text(encoding="utf-8") == '{"k": 1}'
        # The pinned dir's user-chosen perms are NOT silently narrowed...
        assert pinned.stat().st_mode & 0o777 == 0o755
        # ...but the credential file itself is still 0600.
        assert (pinned / "auth.json").stat().st_mode & 0o777 == 0o600

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
