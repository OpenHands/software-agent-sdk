"""Tests for the ACP provider registry."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from openhands.sdk.settings.acp_providers import (
    ACP_PROVIDERS,
    ACPModelOption,
    ACPProviderInfo,
    build_session_model_meta,
    detect_acp_provider_by_agent_name,
    detect_acp_provider_by_command,
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
        # Initial selection rides session/set_model — claude-agent-acp 0.30.0
        # silently ignores the session-_meta payload (#3654), which is still
        # sent as best-effort (session_meta_key below).
        assert info.supports_set_session_model is True
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key == "claudeCode"
        assert info.default_model == "claude-opus-4-8"
        assert any(m.id == "claude-opus-4-8" for m in info.available_models)
        # Pinned binary exposed by the agent-server image wrappers.
        assert info.binary_name == "claude-agent-acp"
        assert info.data_dir_env_var == "CLAUDE_CONFIG_DIR"

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
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key is None
        assert info.default_model == "gpt-5.5/medium"
        assert any(m.id == "gpt-5.5/medium" for m in info.available_models)
        assert info.binary_name == "codex-acp"
        assert info.data_dir_env_var == "CODEX_HOME"

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
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key is None
        assert info.default_model == "auto-gemini-2.5"
        assert any(m.id == "auto-gemini-2.5" for m in info.available_models)
        # The Gemini CLI's ACP binary is just ``gemini`` (the ``--acp`` flag is
        # a trailing arg, preserved by resolve_acp_command on rewrite).
        assert info.binary_name == "gemini"
        # Gemini CLI has no dedicated config-dir var, so only HOME relocates it.
        assert info.data_dir_env_var == "HOME"

    def test_gemini_flash_lite_id_is_not_suffixed_preview(self):
        """gemini-cli 0.45.x serves ``gemini-3.1-flash-lite`` (no ``-preview``).

        The suffixed id was not an accepted model and would 404 if selected.
        """
        ids = {m.id for m in ACP_PROVIDERS["gemini-cli"].available_models}
        assert "gemini-3.1-flash-lite" in ids
        assert "gemini-3.1-flash-lite-preview" not in ids

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


class TestDetectACPProviderByCommand:
    def test_detects_each_provider_from_default_command(self):
        for key, info in ACP_PROVIDERS.items():
            detected = detect_acp_provider_by_command(list(info.default_command))
            assert detected is not None, key
            assert detected.key == key

    def test_tolerates_version_pin(self):
        info = detect_acp_provider_by_command(
            ["npx", "-y", "@google/gemini-cli@0.43.0", "--acp"]
        )
        assert info is not None
        assert info.key == "gemini-cli"

    def test_tolerates_absolute_path_form(self):
        info = detect_acp_provider_by_command(
            ["/usr/local/bin/node", "/opt/node_modules/.bin/codex-acp"]
        )
        assert info is not None
        assert info.key == "codex"

    def test_returns_none_for_custom_command(self):
        assert detect_acp_provider_by_command(["my-custom-acp", "serve"]) is None

    def test_returns_none_for_empty_command(self):
        assert detect_acp_provider_by_command([]) is None

    def test_rejects_incidental_substring_in_custom_command(self):
        # Plain substring matching would misattribute these to codex; the
        # basename + prefix rule rejects them (basenames start with "my-"/"not-").
        assert detect_acp_provider_by_command(["my-codex-acp-wrapper"]) is None
        assert detect_acp_provider_by_command(["/opt/shims/not-codex-acp"]) is None

    def test_prefix_match_accepts_provider_basename_prefix(self):
        # A basename that *starts with* the pattern is treated as that provider
        # (mirrors how "claude-agent" must match the "claude-agent-acp" package).
        info = detect_acp_provider_by_command(["@acme/codex-acp-shim"])
        assert info is not None and info.key == "codex"


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


class TestProviderModelLists:
    """Verify the curated ``available_models`` / ``default_model`` fields."""

    def test_every_builtin_provider_has_available_models(self):
        for key, info in ACP_PROVIDERS.items():
            assert info.available_models, f"{key}: available_models must not be empty"

    def test_available_models_entries_are_model_options(self):
        for info in ACP_PROVIDERS.values():
            for option in info.available_models:
                assert isinstance(option, ACPModelOption)
                assert option.id, "model option id must not be empty"
                assert option.label, "model option label must not be empty"

    def test_model_ids_unique_within_provider(self):
        for key, info in ACP_PROVIDERS.items():
            ids = [m.id for m in info.available_models]
            assert len(ids) == len(set(ids)), f"{key}: duplicate model ids"

    def test_default_model_is_one_of_available_models(self):
        for key, info in ACP_PROVIDERS.items():
            if info.default_model is None:
                continue
            ids = {m.id for m in info.available_models}
            assert info.default_model in ids, (
                f"{key}: default_model {info.default_model!r} not in available_models"
            )

    def test_model_option_is_frozen(self):
        option = ACP_PROVIDERS["claude-code"].available_models[0]
        with pytest.raises((AttributeError, TypeError)):
            option.id = "mutated"  # type: ignore[misc]


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


class TestACPFileSecrets:
    """The registry declares reserved file-content credential secrets for the
    providers that authenticate from a file on disk (issue #1020)."""

    def test_claude_code_has_no_file_secrets(self):
        # Claude Code authenticates via env vars (token / API key) only.
        assert ACP_PROVIDERS["claude-code"].file_secrets == ()

    def test_codex_auth_json_spec(self):
        specs = ACP_PROVIDERS["codex"].file_secrets
        assert len(specs) == 1
        spec = specs[0]
        assert spec.secret_name == "CODEX_AUTH_JSON"
        assert spec.filename == "auth.json"
        assert spec.env_var == "CODEX_HOME"
        assert spec.subdir == "codex"
        assert spec.env_points_to == "dir"

    def test_gemini_vertex_sa_spec(self):
        specs = ACP_PROVIDERS["gemini-cli"].file_secrets
        assert len(specs) == 1
        spec = specs[0]
        assert spec.secret_name == "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        assert spec.filename == "gcloud-credentials.json"
        assert spec.env_var == "GOOGLE_APPLICATION_CREDENTIALS"
        assert spec.subdir == "gemini-cli"
        assert spec.env_points_to == "file"
        # Vertex needs a project + location alongside the SA JSON.
        assert spec.warn_if_unset == ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")

    def test_default_acp_file_secrets_aggregates_all_providers(self):
        from openhands.sdk.settings.acp_providers import default_acp_file_secrets

        specs = default_acp_file_secrets()
        assert {s.secret_name for s in specs} == {
            "CODEX_AUTH_JSON",
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        }
        # Deterministic concatenation in ACP_PROVIDERS registration order
        # (codex before gemini-cli) — downstream callers can rely on a stable
        # ordering of the built-in specs.
        assert specs == (
            ACP_PROVIDERS["codex"].file_secrets
            + ACP_PROVIDERS["gemini-cli"].file_secrets
        )

    def test_file_secret_spec_is_frozen(self):
        from pydantic import ValidationError

        from openhands.sdk.settings.acp_providers import ACPFileSecretSpec

        spec = ACPFileSecretSpec(
            secret_name="X", filename="x.json", env_var="X_HOME", subdir="x"
        )
        with pytest.raises(ValidationError):
            spec.secret_name = "Y"  # type: ignore[misc]

    def test_file_secret_spec_rejects_path_traversal(self):
        from pydantic import ValidationError

        from openhands.sdk.settings.acp_providers import ACPFileSecretSpec

        # filename must be a bare basename.
        with pytest.raises(ValidationError):
            ACPFileSecretSpec(
                secret_name="X", filename="../escape.json", env_var="X", subdir="x"
            )
        with pytest.raises(ValidationError):
            ACPFileSecretSpec(
                secret_name="X", filename="a/b.json", env_var="X", subdir="x"
            )
        # subdir must not escape the acp root.
        with pytest.raises(ValidationError):
            ACPFileSecretSpec(
                secret_name="X", filename="x.json", env_var="X", subdir="../up"
            )
        with pytest.raises(ValidationError):
            ACPFileSecretSpec(
                secret_name="X", filename="x.json", env_var="X", subdir="/abs"
            )
        # "." / whitespace would drop the file straight into the shared acp/ root.
        for bad in (".", "  ", " . "):
            with pytest.raises(ValidationError):
                ACPFileSecretSpec(
                    secret_name="X", filename="x.json", env_var="X", subdir=bad
                )
