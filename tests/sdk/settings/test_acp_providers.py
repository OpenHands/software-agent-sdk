"""Tests for the ACP provider registry."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from openhands.sdk.settings.acp_providers import (
    ACP_PROVIDERS,
    ACPProviderInfo,
    build_session_model_meta,
    detect_acp_provider_by_agent_name,
    get_acp_provider,
)


class TestACPProviderInfo:
    def test_known_providers_are_registered(self):
        assert set(ACP_PROVIDERS) == {"claude-code", "codex", "gemini-cli"}

    def test_all_entries_are_acp_provider_info(self):
        for info in ACP_PROVIDERS.values():
            assert isinstance(info, ACPProviderInfo)

    def test_claude_code_metadata(self):
        info = ACP_PROVIDERS["claude-code"]
        assert info.key == "claude-code"
        assert info.display_name == "Claude Code"
        assert info.default_command[0] == "npx"
        assert "@agentclientprotocol/claude-agent-acp" in info.default_command[-1]
        assert info.api_key_env_var == "ANTHROPIC_API_KEY"
        assert info.base_url_env_var == "ANTHROPIC_BASE_URL"
        assert info.default_session_mode == "bypassPermissions"
        assert "claude-agent" in info.agent_name_patterns
        assert info.supports_set_session_model is False
        assert info.session_meta_key == "claudeCode"

    def test_codex_metadata(self):
        info = ACP_PROVIDERS["codex"]
        assert info.key == "codex"
        assert info.display_name == "Codex"
        assert "@zed-industries/codex-acp" in info.default_command[-1]
        assert info.api_key_env_var == "OPENAI_API_KEY"
        assert info.base_url_env_var == "OPENAI_BASE_URL"
        assert info.default_session_mode == "full-access"
        assert "codex-acp" in info.agent_name_patterns
        assert info.supports_set_session_model is True
        assert info.session_meta_key is None

    def test_gemini_cli_metadata(self):
        info = ACP_PROVIDERS["gemini-cli"]
        assert info.key == "gemini-cli"
        assert info.display_name == "Gemini CLI"
        assert "--acp" in info.default_command
        assert info.api_key_env_var == "GEMINI_API_KEY"
        assert info.base_url_env_var == "GEMINI_BASE_URL"
        assert info.default_session_mode == "yolo"
        assert "gemini-cli" in info.agent_name_patterns
        assert info.supports_set_session_model is True
        assert info.session_meta_key is None

    def test_provider_info_is_frozen(self):
        info = ACP_PROVIDERS["claude-code"]
        with pytest.raises((AttributeError, TypeError)):
            info.key = "mutated"  # type: ignore[misc]

    def test_default_command_is_tuple(self):
        for key, info in ACP_PROVIDERS.items():
            assert isinstance(info.default_command, tuple), (
                f"{key}: default_command must be a tuple"
            )

    def test_acp_providers_is_read_only(self):
        assert isinstance(ACP_PROVIDERS, MappingProxyType)
        with pytest.raises(TypeError):
            ACP_PROVIDERS["new-provider"] = ACP_PROVIDERS["claude-code"]  # type: ignore[index]


class TestGetACPProvider:
    def test_returns_info_for_known_keys(self):
        for key in ("claude-code", "codex", "gemini-cli"):
            result = get_acp_provider(key)
            assert result is not None
            assert result.key == key

    def test_returns_none_for_custom(self):
        assert get_acp_provider("custom") is None

    def test_returns_none_for_unknown(self):
        assert get_acp_provider("nonexistent-provider") is None


class TestDetectACPProviderByAgentName:
    def test_detects_claude_code_by_agent_name(self):
        info = detect_acp_provider_by_agent_name("claude-agent-acp v0.29.0")
        assert info is not None
        assert info.key == "claude-code"

    def test_detects_codex_by_agent_name(self):
        info = detect_acp_provider_by_agent_name("codex-acp")
        assert info is not None
        assert info.key == "codex"

    def test_detects_gemini_cli_by_agent_name(self):
        info = detect_acp_provider_by_agent_name("gemini-cli 0.38.0")
        assert info is not None
        assert info.key == "gemini-cli"

    def test_case_insensitive_detection(self):
        assert detect_acp_provider_by_agent_name("CLAUDE-AGENT-ACP") is not None
        assert detect_acp_provider_by_agent_name("Gemini-CLI") is not None

    def test_returns_none_for_unknown_agent_name(self):
        assert detect_acp_provider_by_agent_name("some-unknown-agent") is None

    def test_returns_none_for_empty_string(self):
        assert detect_acp_provider_by_agent_name("") is None


class TestProviderRegistryConsistency:
    """Verify the registry is internally consistent."""

    def test_every_provider_has_non_empty_default_command(self):
        for key, info in ACP_PROVIDERS.items():
            assert info.default_command, f"{key}: default_command must not be empty"

    def test_every_provider_has_agent_name_patterns(self):
        for key, info in ACP_PROVIDERS.items():
            assert info.agent_name_patterns, (
                f"{key}: agent_name_patterns must not be empty"
            )

    def test_every_provider_has_non_empty_session_mode(self):
        for key, info in ACP_PROVIDERS.items():
            assert info.default_session_mode, (
                f"{key}: default_session_mode must not be empty"
            )

    def test_session_modes_are_distinct(self):
        modes = [info.default_session_mode for info in ACP_PROVIDERS.values()]
        assert len(modes) == len(set(modes)), "each provider should use a unique mode"

    def test_detect_returns_matching_provider_for_all_registered_patterns(self):
        """Every registered pattern should resolve back to its own provider."""
        for key, info in ACP_PROVIDERS.items():
            for pattern in info.agent_name_patterns:
                detected = detect_acp_provider_by_agent_name(pattern)
                assert detected is not None, (
                    f"pattern {pattern!r} did not match any provider"
                )
                assert detected.key == key, (
                    f"pattern {pattern!r} matched {detected.key!r}, expected {key!r}"
                )


class TestBuildSessionModelMeta:
    def test_empty_when_no_model(self):
        assert build_session_model_meta("claude-agent-acp", None) == {}
        assert build_session_model_meta("claude-agent-acp", "") == {}

    def test_claude_uses_meta_key(self):
        result = build_session_model_meta("claude-agent-acp v0.29.0", "claude-opus-4")
        assert result == {"claudeCode": {"options": {"model": "claude-opus-4"}}}

    def test_codex_returns_empty(self):
        result = build_session_model_meta("codex-acp", "gpt-4o")
        assert result == {}

    def test_gemini_returns_empty(self):
        result = build_session_model_meta("gemini-cli 0.38.0", "gemini-2.0-flash")
        assert result == {}

    def test_unknown_agent_returns_empty(self):
        result = build_session_model_meta("unknown-agent", "some-model")
        assert result == {}
