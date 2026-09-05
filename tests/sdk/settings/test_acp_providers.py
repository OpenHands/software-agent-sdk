"""Tests for the ACP provider registry."""

from __future__ import annotations

from types import MappingProxyType
from typing import get_args

import pytest

from openhands.sdk.settings.acp_install_catalog import (
    PI_ACP_VERSION,
    PI_CODING_AGENT_VERSION,
)
from openhands.sdk.settings.acp_providers import (
    ACP_PROVIDERS,
    ACPModelOption,
    ACPProviderInfo,
    build_session_model_meta,
    detect_acp_provider_by_agent_name,
    detect_acp_provider_by_command,
    get_acp_provider,
)
from openhands.sdk.settings.model import ACPServerKind


class TestACPProviderInfo:
    def test_known_providers_are_registered(self):
        # A registry addition cannot be silent, but the guard that makes it so
        # is test_acp_server_kind_matches_registry_keys — it ties the registry
        # to the ACPServerKind Literal, which is the edit that actually has to
        # happen. Restating the key set here only adds a literal every provider
        # PR has to update, so this asserts the shared invariants instead.
        assert ACP_PROVIDERS, "the registry must not be empty"
        for key, info in ACP_PROVIDERS.items():
            assert info.key == key, f"{key}: registry key does not match info.key"

    def test_all_entries_are_acp_provider_info(self):
        for info in ACP_PROVIDERS.values():
            assert isinstance(info, ACPProviderInfo)

    def test_default_commands_prefer_offline_cache(self):
        for info in ACP_PROVIDERS.values():
            assert info.default_command[:3] == ("npx", "-y", "--prefer-offline")

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
        # Initial selection rides a protocol call — claude-agent-acp ignores the
        # session-_meta payload (#3654), which is still sent as best-effort
        # (session_meta_key below). On 0.44.0 the call is set_config_option.
        assert info.supports_set_session_model is True
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key == "claudeCode"
        assert info.default_model == "opus[1m]"
        models = {model.id: model.label for model in info.available_models}
        assert models["opus[1m]"] == "Claude Opus (1M)"
        assert models["claude-opus-5"] == "Claude Opus 5"
        assert models["sonnet"] == "Claude Sonnet"
        assert models["haiku"] == "Claude Haiku"
        # Pinned binary exposed by the agent-server image wrappers.
        assert info.binary_name == "claude-agent-acp"
        assert info.data_dir_env_var == "CLAUDE_CONFIG_DIR"

    def test_codex_metadata(self):
        info = ACP_PROVIDERS["codex"]
        assert info.key == "codex"
        assert info.display_name == "Codex"
        assert "@agentclientprotocol/codex-acp" in info.default_command[-1]
        assert info.api_key_env_var == "OPENAI_API_KEY"
        assert info.base_url_env_var == "OPENAI_BASE_URL"
        assert info.default_session_mode == "agent-full-access"
        assert "codex-acp" in info.agent_name_patterns
        assert info.supports_set_session_model is True
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key is None
        assert info.default_model == "gpt-5.5"
        assert any(m.id == "gpt-5.6-sol" for m in info.available_models)
        assert any(m.id == "gpt-5.6-terra" for m in info.available_models)
        assert any(m.id == "gpt-5.6-luna" for m in info.available_models)
        assert any(m.id == "gpt-5.5" for m in info.available_models)
        assert info.binary_name == "codex-acp"
        assert info.data_dir_env_var == "CODEX_HOME"

    def test_gemini_cli_metadata(self):
        info = ACP_PROVIDERS["gemini-cli"]
        assert info.key == "gemini-cli"
        assert info.display_name == "Gemini CLI"
        assert "--acp" in info.default_command
        assert info.api_key_env_var == "GEMINI_API_KEY"
        assert info.base_url_env_var == "GEMINI_BASE_URL"
        assert info.default_session_mode == "default"
        assert "gemini-cli" in info.agent_name_patterns
        assert info.supports_set_session_model is True
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key is None
        assert info.default_model == "auto"
        assert any(m.id == "auto" for m in info.available_models)
        # The Gemini CLI's ACP binary is just ``gemini`` (the ``--acp`` flag is
        # a trailing arg, preserved by resolve_acp_command on rewrite).
        assert info.binary_name == "gemini"
        # Gemini CLI has no dedicated config-dir var, so only HOME relocates it.
        assert info.data_dir_env_var == "HOME"

    def test_kimi_code_metadata(self):
        info = ACP_PROVIDERS["kimi-code"]
        assert info.key == "kimi-code"
        assert info.display_name == "Kimi Code"
        # Scoped package only: the unscoped npm ``kimi-code`` is a third-party
        # tool that also ships a ``kimi`` bin.
        assert info.default_command[:3] == ("npx", "-y", "--prefer-offline")
        assert "@moonshot-ai/kimi-code@" in info.default_command[3]
        assert info.default_command[4] == "acp"
        assert info.api_key_env_var is None
        assert info.base_url_env_var == "KIMI_BASE_URL"
        assert info.default_session_mode == "yolo"
        assert "kimi" in info.agent_name_patterns
        assert info.supports_set_session_model is True
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key is None
        # Model ids follow the credential, not the plan tier, so nothing is
        # curated and the CLI resolves its own default. See
        # _UNCURATED_MODEL_PROVIDERS.
        assert info.available_models == ()
        assert info.default_model is None
        # The CLI's ACP binary is just ``kimi``; ``acp`` is a trailing arg.
        assert info.binary_name == "kimi"
        assert info.data_dir_env_var == "KIMI_CODE_HOME"
        # The credential is config.toml materialised into KIMI_CODE_HOME —
        # the env var authenticates nothing on its own.
        assert [s.secret_name for s in info.file_secrets] == ["KIMI_CODE_CONFIG_TOML"]
        spec = info.file_secrets[0]
        assert spec.filename == "config.toml"
        assert spec.env_var == "KIMI_CODE_HOME"
        assert spec.env_points_to == "dir"

    def test_pi_metadata(self):
        info = ACP_PROVIDERS["pi"]
        assert info.key == "pi"
        assert info.display_name == "Pi"
        # pi-acp only adapts; it spawns the separately pinned `pi` engine off
        # PATH, so the npx default provisions both packages and runs pi-acp.
        assert info.default_command == (
            "npx",
            "-y",
            "--prefer-offline",
            f"--package=pi-acp@{PI_ACP_VERSION}",
            f"--package=@earendil-works/pi-coding-agent@{PI_CODING_AGENT_VERSION}",
            "pi-acp",
        )
        assert info.api_key_env_var == "ANTHROPIC_API_KEY"
        # pi takes its base URL from its own catalogue, not the environment.
        assert info.base_url_env_var is None
        # pi-acp maps ACP modes onto thinking levels and has no permission mode.
        assert info.default_session_mode is None
        assert info.agent_name_patterns == ("pi-acp",)
        assert info.supports_set_session_model is True
        assert info.supports_runtime_model_switch is True
        assert info.session_meta_key is None
        assert info.default_model is None
        assert info.binary_name == "pi-acp"
        assert info.data_dir_env_var == "HOME"
        assert [spec.secret_name for spec in info.file_secrets] == ["PI_AUTH_JSON"]

    def test_pi_credential_file_is_auth_json_in_the_config_dir(self):
        """PI_CODING_AGENT_DIR names the directory pi reads auth.json from;
        settings.json in the same directory does not authenticate."""
        (spec,) = ACP_PROVIDERS["pi"].file_secrets
        assert spec.filename == "auth.json"
        assert spec.env_var == "PI_CODING_AGENT_DIR"
        assert spec.env_points_to == "dir"
        assert spec.subdir == "pi"

    def test_opencode_metadata(self):
        info = ACP_PROVIDERS["opencode"]
        assert info.key == "opencode"
        assert info.display_name == "OpenCode"
        assert info.default_command[:3] == ("npx", "-y", "--prefer-offline")
        assert "opencode-ai@" in info.default_command[3]
        # ``acp`` is the subcommand that puts the CLI into ACP mode; it is
        # preserved as a trailing arg when resolve_acp_command rewrites to the
        # pinned binary.
        assert info.default_command[4] == "acp"
        assert info.api_key_env_var == "OPENCODE_API_KEY"
        # OpenCode Zen resolves its endpoint from the model catalogue, so there
        # is no base-URL override var.
        assert info.base_url_env_var is None
        # OpenCode's only modes are ``build`` and ``plan``.
        assert info.default_session_mode == "build"
        assert "opencode" in info.agent_name_patterns
        assert info.supports_set_session_model is True
        assert info.supports_runtime_model_switch is True
        # session/new ignores a _meta model selection.
        assert info.session_meta_key is None
        assert info.default_model == "opencode/big-pickle"
        model_ids = [m.id for m in info.available_models]
        assert "opencode/big-pickle" in model_ids
        assert "opencode/claude-opus-5" in model_ids
        # One free-tier id, which is the only kind that works with no key.
        assert "opencode/nemotron-3-ultra-free" in model_ids
        assert info.binary_name == "opencode"
        # OPENCODE_CONFIG_DIR relocates only the config file; auth, sessions and
        # caches follow XDG dirs that fall back to HOME.
        assert info.data_dir_env_var == "HOME"
        # OPENCODE_AUTH_CONTENT carries the auth store, so there is no
        # credential file to materialise.
        assert info.file_secrets == ()

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
        for key in ACP_PROVIDERS:
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

    def test_detects_kimi_code_by_agent_name(self):
        # ``kimi acp`` reports agentInfo.name "Kimi Code CLI".
        info = detect_acp_provider_by_agent_name("Kimi Code CLI")
        assert info is not None
        assert info.key == "kimi-code"

    def test_detects_opencode_by_agent_name(self):
        # ``opencode acp`` reports agentInfo.name "OpenCode".
        info = detect_acp_provider_by_agent_name("OpenCode")
        assert info is not None
        assert info.key == "opencode"

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
            # Identity, not just a matching key: detect_acp_provider_by_command
            # must resolve back to the very same ACP_PROVIDERS entry.
            assert detected is info, key

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

    def test_detects_kimi_code_by_command(self):
        # Deliberately not the pinned version: detection matches on package
        # name, so a client may send any version.
        info = detect_acp_provider_by_command(
            ["npx", "-y", "@moonshot-ai/kimi-code@0.39.1", "acp"]
        )
        assert info is not None
        assert info.key == "kimi-code"
        info = detect_acp_provider_by_command(["/opt/acp-wrappers/kimi", "acp"])
        assert info is not None
        assert info.key == "kimi-code"

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

    def test_session_mode_is_a_real_id_or_absent(self):
        """A mode id is scoped to its own server, so the only thing worth
        asserting registry-side is that a recorded one is a usable id.

        Two providers sharing a value says nothing — ``default`` is a mode
        several ACP servers offer, and picking it for a second provider is a
        correct record, not a collision. Whether the server accepts the id is
        checked where it can be: against the running server, in
        tests/sdk/agent/test_acp_conformance.py.
        """
        for key, info in ACP_PROVIDERS.items():
            mode = info.default_session_mode
            assert mode is None or (isinstance(mode, str) and mode.strip()), (
                f"{key}: default_session_mode must be a non-empty id or None"
            )

    def test_session_mode_is_a_non_empty_string_or_none(self):
        """``None`` skips the ``session/set_mode`` call; an empty string would
        be sent verbatim and rejected by the server."""
        for key, info in ACP_PROVIDERS.items():
            assert (
                info.default_session_mode is None or info.default_session_mode.strip()
            ), (  # noqa: E501
                f"{key}: default_session_mode must be None or non-blank"
            )

    def test_session_modes_are_distinct(self):
        modes = [
            info.default_session_mode
            for info in ACP_PROVIDERS.values()
            if info.default_session_mode is not None
        ]
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

    def test_acp_server_kind_matches_registry_keys(self):
        # ACPServerKind (the ACPAgentSettings.acp_server discriminator) is a
        # hand-maintained Literal, not derived from ACP_PROVIDERS — a provider
        # added to one without the other would either be unselectable via
        # settings (Literal missing it) or unrecognized at runtime (registry
        # missing it). "custom" is the one non-registry value it also accepts.
        assert set(get_args(ACPServerKind)) == set(ACP_PROVIDERS) | {"custom"}


# Providers whose model ids depend on which credential the user supplies, so no
# static list can be correct. Not the same as plan-tier variance, which the
# curated lists already tolerate (they are suggestions, not access checks).
_UNCURATED_MODEL_PROVIDERS = {
    # An account login offers ``kimi-code/*`` aliases; a config.toml provider
    # offers whatever alias the user named. A static list would be wrong, not
    # merely incomplete, for anyone not on an account login.
    "kimi-code",
}


class TestProviderModelLists:
    """Verify the curated ``available_models`` / ``default_model`` fields."""

    def test_every_builtin_provider_has_available_models(self):
        for key, info in ACP_PROVIDERS.items():
            if key in _UNCURATED_MODEL_PROVIDERS:
                assert not info.available_models, (
                    f"{key}: now curates models — remove it from "
                    "_UNCURATED_MODEL_PROVIDERS"
                )
                assert info.default_model is None, (
                    f"{key}: an uncurated provider must leave default_model "
                    "None so the CLI resolves its own default"
                )
                continue
            assert info.available_models, f"{key}: available_models must not be empty"

    def test_every_uncurated_provider_is_a_real_registry_key(self):
        assert _UNCURATED_MODEL_PROVIDERS <= set(ACP_PROVIDERS)

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
            "KIMI_CODE_CONFIG_TOML",
            "PI_AUTH_JSON",
        }
        # Deterministic concatenation in ACP_PROVIDERS registration order
        # (codex, gemini-cli, kimi-code, pi) — downstream callers can rely on a
        # stable ordering of the built-in specs.
        assert specs == (
            ACP_PROVIDERS["codex"].file_secrets
            + ACP_PROVIDERS["gemini-cli"].file_secrets
            + ACP_PROVIDERS["kimi-code"].file_secrets
            + ACP_PROVIDERS["pi"].file_secrets
        )

    def test_file_secret_subdirs_are_unique_across_providers(self):
        # Each spec's subdir is a folder under the shared per-conversation
        # acp/ root (see ACPFileSecretSpec.subdir); a collision would let two
        # providers' credential files overwrite each other.
        subdirs = [
            spec.subdir for info in ACP_PROVIDERS.values() for spec in info.file_secrets
        ]
        assert len(subdirs) == len(set(subdirs)), subdirs

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
