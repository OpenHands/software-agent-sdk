"""Tests for ``resolve_agent_profile`` / ``resolve_agent_profile_dry_run``.

Covers both union variants, the null/empty/filter/dangling MCP cases, the
dangling-LLM hard error, the ``skills[].mcp_tools`` decryption the resolver is
responsible for, and the dry-run's redacted, side-effect-free diagnostics.
"""

from pathlib import Path

import pytest
from fastmcp.mcp_config import MCPConfig
from pydantic import SecretStr

from openhands.sdk.agent import ACPAgent, Agent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.profiles import (
    ACPAgentProfile,
    AgentProfileStore,
    DanglingMcpServerRef,
    OpenHandsAgentProfile,
    ProfileNotFound,
    resolve_agent_profile,
    resolve_agent_profile_dry_run,
)
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX, Cipher


_LLM_SECRET = "sk-LLM-SECRET-SHOULD-NOT-LEAK"
_MCP_SECRET = "ghp_MCP_SECRET_SHOULD_NOT_LEAK"


@pytest.fixture
def llm_store(tmp_path: Path) -> LLMProfileStore:
    store = LLMProfileStore(base_dir=tmp_path / "llm")
    store.save(
        "default",
        LLM(model="gpt-4o", api_key=SecretStr(_LLM_SECRET), usage_id="x"),
        include_secrets=True,
    )
    return store


@pytest.fixture
def mcp_config() -> MCPConfig:
    return MCPConfig.model_validate(
        {
            "mcpServers": {
                "fetch": {
                    "url": "https://fetch.test",
                    "headers": {"Authorization": f"Bearer {_MCP_SECRET}"},
                },
                "other": {"command": "echo", "args": ["hi"]},
            }
        }
    )


# --------------------------------------------------------------------------- #
# OpenHands path
# --------------------------------------------------------------------------- #


def test_openhands_resolves_to_settings_with_injected_llm(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh",
        llm_profile_ref="default",
        agent="CodeActAgent",
        system_message_suffix="be terse",
        enable_sub_agents=True,
        tool_concurrency_limit=3,
        mcp_server_refs=["fetch"],
    )
    settings = resolve_agent_profile(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent == "CodeActAgent"
    assert settings.enable_sub_agents is True
    assert settings.tool_concurrency_limit == 3
    assert settings.agent_context is not None
    assert settings.agent_context.system_message_suffix == "be terse"
    # LLM injected with the concrete (decrypted) credential.
    assert isinstance(settings.llm.api_key, SecretStr)
    assert settings.llm.api_key.get_secret_value() == _LLM_SECRET
    # MCP filtered to the referenced key.
    assert settings.mcp_config is not None
    assert list(settings.mcp_config.mcpServers.keys()) == ["fetch"]
    # Output feeds the unchanged create_agent path.
    assert isinstance(settings.create_agent(), Agent)


def test_openhands_copies_skills_and_verification(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh",
        llm_profile_ref="default",
        skills=[Skill(name="s1", content="do x")],
    )
    profile.verification.critic_enabled = True
    profile.verification.critic_model_name = "critic-x"

    settings = resolve_agent_profile(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert [s.name for s in settings.agent_context.skills] == ["s1"]
    assert settings.verification.critic_enabled is True
    assert settings.verification.critic_model_name == "critic-x"
    # The profile carries no critic_api_key; it defaults to None on resolve.
    assert settings.verification.critic_api_key is None


def test_missing_llm_ref_raises_profile_not_found(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="does-not-exist")
    with pytest.raises(ProfileNotFound):
        resolve_agent_profile(
            profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
        )


# --------------------------------------------------------------------------- #
# MCP composition
# --------------------------------------------------------------------------- #


def test_mcp_null_refs_passes_config_through(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=None
    )
    settings = resolve_agent_profile(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert settings.mcp_config is mcp_config
    assert settings.mcp_config is not None
    assert set(settings.mcp_config.mcpServers.keys()) == {"fetch", "other"}


def test_mcp_empty_refs_means_none(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=[]
    )
    settings = resolve_agent_profile(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert settings.mcp_config is None


def test_mcp_filter_selects_named_keys(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["other"]
    )
    settings = resolve_agent_profile(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert settings.mcp_config is not None
    assert list(settings.mcp_config.mcpServers.keys()) == ["other"]


def test_mcp_dangling_ref_raises(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch", "missing"]
    )
    with pytest.raises(DanglingMcpServerRef) as exc:
        resolve_agent_profile(
            profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
        )
    assert exc.value.missing == ["missing"]


def test_mcp_dangling_when_config_is_none(
    llm_store: LLMProfileStore,
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch"]
    )
    with pytest.raises(DanglingMcpServerRef) as exc:
        resolve_agent_profile(
            profile, llm_store=llm_store, mcp_config=None, cipher=None
        )
    assert exc.value.missing == ["fetch"]


# --------------------------------------------------------------------------- #
# skills[].mcp_tools decryption (the resolver's responsibility)
# --------------------------------------------------------------------------- #


def test_resolver_decrypts_skill_mcp_tools(tmp_path: Path) -> None:
    cipher = Cipher("k" * 64)
    secret = "ghp_SKILL_MCP_SECRET"
    skill = Skill(
        name="leaky",
        content="x",
        mcp_tools={
            "mcpServers": {
                "svc": {
                    "url": "https://svc.test",
                    "headers": {"Authorization": f"Bearer {secret}"},
                    "env": {"API_KEY": secret},
                }
            }
        },
    )
    lstore = LLMProfileStore(base_dir=tmp_path / "llm")
    lstore.save(
        "default",
        LLM(model="gpt-4o", api_key=SecretStr("sk-x"), usage_id="x"),
        include_secrets=True,
    )
    astore = AgentProfileStore(base_dir=tmp_path / "agent")
    astore.save(
        OpenHandsAgentProfile(name="p", llm_profile_ref="default", skills=[skill]),
        cipher=cipher,
    )

    # Loaded with cipher, the skill's mcp_tools secrets are still ciphertext —
    # Skill has a masking serializer but no decrypting validator.
    loaded = astore.load("p", cipher=cipher)
    assert isinstance(loaded, OpenHandsAgentProfile)
    stored_tools = loaded.skills[0].mcp_tools
    assert stored_tools is not None
    stored = stored_tools["mcpServers"]["svc"]
    assert secret not in stored["env"]["API_KEY"]
    assert stored["env"]["API_KEY"].startswith(FERNET_TOKEN_PREFIX)

    # The resolver holds the cipher and decrypts them for execution.
    settings = resolve_agent_profile(
        loaded, llm_store=lstore, mcp_config=None, cipher=cipher
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    resolved_tools = settings.agent_context.skills[0].mcp_tools
    assert resolved_tools is not None
    resolved = resolved_tools["mcpServers"]["svc"]
    assert resolved["headers"]["Authorization"] == f"Bearer {secret}"
    assert resolved["env"]["API_KEY"] == secret


# --------------------------------------------------------------------------- #
# ACP path
# --------------------------------------------------------------------------- #


def test_acp_resolves_to_settings_without_credentials(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = ACPAgentProfile(
        name="acp",
        acp_server="codex",
        acp_model="gpt-5.5/medium",
        acp_session_mode="full-access",
        acp_command="codex-acp --foo",
        acp_args=["--flag"],
        mcp_server_refs=["fetch"],
    )
    settings = resolve_agent_profile(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert isinstance(settings, ACPAgentSettings)
    assert settings.acp_server == "codex"
    assert settings.acp_model == "gpt-5.5/medium"
    assert settings.acp_session_mode == "full-access"
    # str command is tokenized into the settings' list[str] field.
    assert settings.acp_command == ["codex-acp", "--foo"]
    assert settings.acp_args == ["--flag"]
    assert settings.mcp_config is not None
    assert list(settings.mcp_config.mcpServers.keys()) == ["fetch"]
    # No credential is injected; the deprecated llm channel stays empty.
    assert settings.llm.api_key is None
    assert isinstance(settings.create_agent(), ACPAgent)


def test_acp_blank_command_resolves_empty_list(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    settings = resolve_agent_profile(
        profile, llm_store=llm_store, mcp_config=None, cipher=None
    )
    assert isinstance(settings, ACPAgentSettings)
    assert settings.acp_command == []
    assert settings.acp_args == []


# --------------------------------------------------------------------------- #
# Dry-run
# --------------------------------------------------------------------------- #


def test_dry_run_openhands_valid_and_redacted(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch"]
    )
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert diag.agent_kind == "openhands"
    assert diag.valid is True
    assert diag.errors == []
    assert diag.llm_profile_ref == "default"
    assert diag.llm_profile_resolved is True
    assert diag.llm_api_key_set is True
    assert diag.resolved_mcp_servers == ["fetch"]
    assert diag.dangling_mcp_server_refs == []
    assert diag.resolved_settings is not None
    # No secret survives into the redacted resolved settings.
    dumped = diag.model_dump_json()
    assert _LLM_SECRET not in dumped
    assert _MCP_SECRET not in dumped


def test_dry_run_reports_dangling_llm_and_mcp(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="nope", mcp_server_refs=["missing"]
    )
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert diag.valid is False
    assert diag.llm_profile_resolved is False
    assert diag.dangling_mcp_server_refs == ["missing"]
    assert len(diag.errors) == 2
    # Invalid => no resolved settings produced.
    assert diag.resolved_settings is None


def test_dry_run_total_on_llm_store_transient_error(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    # The store can raise filelock.TimeoutError (lock contention) before its
    # own handler runs; the dry-run must surface that as a diagnostic, not
    # crash the editor preview (#3719).
    def _boom(*_args: object, **_kwargs: object) -> LLM:
        raise TimeoutError("profile store lock acquisition timed out")

    llm_store.load = _boom  # type: ignore[method-assign]
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch"]
    )
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert diag.valid is False
    assert diag.llm_profile_resolved is False
    # Reported as "could not load" (transient), distinct from "not found".
    assert any("Could not load LLM profile" in e for e in diag.errors)
    assert diag.resolved_settings is None


def test_dry_run_verdict_matches_real_resolve(
    llm_store: LLMProfileStore, mcp_config: MCPConfig
) -> None:
    # A dangling MCP ref: dry-run says invalid, real resolve raises.
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["missing"]
    )
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
    )
    assert diag.valid is False
    with pytest.raises(DanglingMcpServerRef):
        resolve_agent_profile(
            profile, llm_store=llm_store, mcp_config=mcp_config, cipher=None
        )


def test_dry_run_acp_reports_credential_channels_by_role(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp", acp_server="codex")
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=None, cipher=None
    )
    assert diag.agent_kind == "acp"
    assert diag.valid is True
    # The API key and the file-content credential are alternative auth paths;
    # the base URL is optional proxy routing — each surfaced in its own field.
    assert diag.acp_api_key_secret_name == "OPENAI_API_KEY"
    assert diag.acp_base_url_secret_name == "OPENAI_BASE_URL"
    assert diag.acp_file_secret_names == ["CODEX_AUTH_JSON"]
    assert diag.resolved_settings is not None


def test_dry_run_acp_custom_server_has_no_credential_channels(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(
        name="acp", acp_server="custom", acp_command="my-acp-server"
    )
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=None, cipher=None
    )
    assert diag.acp_api_key_secret_name is None
    assert diag.acp_base_url_secret_name is None
    assert diag.acp_file_secret_names == []


def test_custom_acp_without_command_is_invalid(
    llm_store: LLMProfileStore,
) -> None:
    # A custom server has no default launch command, so the resolved settings
    # would fail in create_agent(). The dry-run must report valid=False and the
    # strict resolve must raise, rather than deferring the failure to start.
    profile = ACPAgentProfile(name="acp", acp_server="custom")
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=None, cipher=None
    )
    assert diag.valid is False
    assert diag.errors
    assert diag.resolved_settings is None
    with pytest.raises(ValueError):
        resolve_agent_profile(
            profile, llm_store=llm_store, mcp_config=None, cipher=None
        )


def test_dry_run_normalizes_settings_build_failure(
    llm_store: LLMProfileStore,
) -> None:
    # An unbalanced-quote acp_command passes profile validation but breaks
    # shlex.split during settings construction; the dry-run must report it as
    # invalid rather than raising (its contract is total).
    profile = ACPAgentProfile(
        name="acp", acp_server="custom", acp_command="unterminated 'quote"
    )
    diag = resolve_agent_profile_dry_run(
        profile, llm_store=llm_store, mcp_config=None, cipher=None
    )
    assert diag.valid is False
    assert diag.errors
    assert diag.resolved_settings is None
