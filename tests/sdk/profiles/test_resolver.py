"""Tests for ``resolve_agent_profile`` / ``resolve_agent_profile_dry_run``.

Covers both union variants, the null/empty/filter/dangling MCP cases, the
dangling-LLM hard error, and the dry-run's redacted, side-effect-free
diagnostics. Profiles no longer embed ``skills`` (#4017) — the skill catalog is
server-discovered and filtered by the ``disabled_skills`` deny-list (never an
allow-list, which could dangle).
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import ACPAgent, Agent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.profiles import (
    ACPAgentProfile,
    DanglingMcpServerRef,
    OpenHandsAgentProfile,
    ProfileNotFound,
    resolve_agent_profile,
    resolve_agent_profile_dry_run,
)
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool


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
def mcp_config() -> dict[str, MCPServer]:
    return coerce_mcp_config(
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
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
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
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
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
    assert settings.mcp_config != {}
    assert list(settings.mcp_config.keys()) == ["fetch"]
    # The profile's tools default (None) rides through so create_agent is the
    # single defaulting point (#3967 / #3978); the built agent carries the
    # standard exec set plus the sub-agent tool set (enable_sub_agents=True).
    assert settings.tools is None
    agent = settings.create_agent()
    assert isinstance(agent, Agent)
    agent_tool_names = [t.name for t in agent.tools]
    assert {"terminal", "file_editor", "task_tracker"} <= set(agent_tool_names)
    assert "task_tool_set" in agent_tool_names


def test_openhands_resolves_default_exec_tools(
    llm_store: LLMProfileStore,
) -> None:
    """A profile with no explicit ``tools`` resolves to ``tools=None``, and
    ``create_agent`` attaches the standard exec set (#3967) — otherwise the
    agent has only the Finish/Think built-ins and no way to run shell commands
    or edit files. The sub-agent tool set stays out when ``enable_sub_agents``
    is False (default); browser is a serving-layer injection, never part of
    the deterministic default (see tests/sdk/tool/test_defaults.py)."""
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    assert profile.enable_sub_agents is False
    assert profile.tools is None

    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.tools is None
    # The built agent carries the exec tools, not just the built-ins.
    agent = settings.create_agent()
    assert [t.name for t in agent.tools] == [
        "terminal",
        "file_editor",
        "task_tracker",
    ]


def test_openhands_profile_tools_selection_is_passed_through(
    llm_store: LLMProfileStore,
) -> None:
    """An explicit profile ``tools`` list is authoritative: used exactly as
    given ([] = deliberately bare), independent of ``enable_sub_agents``."""
    picked = OpenHandsAgentProfile(
        name="picked",
        llm_profile_ref="default",
        tools=[Tool(name="terminal")],
        enable_sub_agents=True,
    )
    settings = resolve_agent_profile(
        picked,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.tools == [Tool(name="terminal")]
    assert [t.name for t in settings.create_agent().tools] == ["terminal"]

    bare = OpenHandsAgentProfile(name="bare", llm_profile_ref="default", tools=[])
    settings = resolve_agent_profile(
        bare,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.tools == []
    assert settings.create_agent().tools == []


def test_openhands_copies_verification(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    profile.verification.critic_enabled = True
    profile.verification.critic_model_name = "critic-x"

    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.verification.critic_enabled is True
    assert settings.verification.critic_model_name == "critic-x"
    # The profile carries no critic_api_key; it defaults to None on resolve.
    assert settings.verification.critic_api_key is None


def test_openhands_resolve_sets_load_project_skills(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    """Project skills are repo-scoped and can't be resolved at profile-resolve
    time (no workspace yet); ``LocalConversation`` loads them lazily on first
    use, gated on ``load_project_skills`` (#4016). The resolver must set it,
    since the resolved ``AgentContext`` otherwise defaults it False."""
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent_context is not None
    assert settings.agent_context.load_project_skills is True


def test_missing_llm_ref_raises_profile_not_found(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="does-not-exist")
    with pytest.raises(ProfileNotFound):
        resolve_agent_profile(
            profile,
            llm_store=llm_store,
            mcp_config=mcp_config,
            available_skills=None,
            cipher=None,
        )


# --------------------------------------------------------------------------- #
# enable_switch_llm_tool (#3856)
# --------------------------------------------------------------------------- #


def test_enable_switch_llm_tool_defaults_true_threads_through(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    # Defaults True to match the global agent settings default.
    assert settings.enable_switch_llm_tool is True


def test_enable_switch_llm_tool_false_threads_through(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", enable_switch_llm_tool=False
    )
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.enable_switch_llm_tool is False


# --------------------------------------------------------------------------- #
# disabled_skills deny-list over discovered skills (#4017)
# --------------------------------------------------------------------------- #


def _discovered_skills() -> list[Skill]:
    return [
        Skill(name="alpha", content="a"),
        Skill(name="beta", content="b"),
        Skill(name="gamma", content="c"),
    ]


def test_disabled_skills_empty_includes_all_discovered(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # The default deny-list ([]) keeps every discovered skill.
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    assert profile.disabled_skills == []
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert {s.name for s in settings.agent_context.skills} == {
        "alpha",
        "beta",
        "gamma",
    }
    # The deny-list is carried onto the context so the lazy project-skill load
    # honors it too.
    assert settings.agent_context.disabled_skills == []


def test_disabled_skills_excludes_named(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # Disabling one skill drops exactly it; the rest of the catalog remains.
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", disabled_skills=["beta"]
    )
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert {s.name for s in settings.agent_context.skills} == {"alpha", "gamma"}
    assert settings.agent_context.disabled_skills == ["beta"]


def test_disabled_skills_missing_name_is_noop(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # The #4017 fix: a disabled name absent from the catalog is a harmless no-op
    # — resolution succeeds (never DanglingSkillRef) and yields all catalog
    # skills. This is the whole point of the deny-list over an allow-list.
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", disabled_skills=["not-in-catalog"]
    )
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert {s.name for s in settings.agent_context.skills} == {
        "alpha",
        "beta",
        "gamma",
    }


def test_acp_profile_gets_no_user_public_skills(
    llm_store: LLMProfileStore,
) -> None:
    # ACP profiles carry no skill field — the subprocess owns its context, so no
    # user/public discovered skills are injected regardless of the catalog.
    acp = ACPAgentProfile(name="acp", acp_server="claude-code")
    assert not hasattr(acp, "skill_refs")
    assert not hasattr(acp, "disabled_skills")
    acp_settings = resolve_agent_profile(
        acp,
        llm_store=llm_store,
        mcp_config={},
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert isinstance(acp_settings, ACPAgentSettings)
    assert acp_settings.agent_context is not None
    assert acp_settings.agent_context.skills == []


def test_no_available_skills_yields_no_skills(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # available_skills=None (no discovery run) → no user/public skills reach the
    # agent. Profiles no longer embed skills (#4017), so there is no other
    # source (project skills load separately in LocalConversation).
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent_context.skills == []


def test_seed_then_resolve_with_narrower_catalog_does_not_dangle(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # #4017 regression: a global whose AgentContext carries an inline skill whose
    # name is NOT in the launch discovery catalog. The old freeze-by-name seed
    # would name that skill and then hard-fail at launch (DanglingSkillRef). The
    # deny-list seed disables nothing, so seed->resolve against the narrower
    # catalog succeeds and yields exactly the catalog skills.
    from openhands.sdk.profiles import build_seed_profile
    from openhands.sdk.settings.model import validate_agent_settings

    settings = validate_agent_settings(
        {
            "agent_kind": "openhands",
            "agent_context": {"skills": [{"name": "inline-only", "content": "x"}]},
        }
    )
    profile = build_seed_profile(settings, active_llm_profile="default")
    assert isinstance(profile, OpenHandsAgentProfile)
    assert profile.disabled_skills == []

    # Launch catalog does NOT contain "inline-only" — must still resolve.
    resolved = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert isinstance(resolved, OpenHandsAgentSettings)
    assert {s.name for s in resolved.agent_context.skills} == {
        "alpha",
        "beta",
        "gamma",
    }


# --------------------------------------------------------------------------- #
# MCP composition
# --------------------------------------------------------------------------- #


def test_mcp_null_refs_passes_config_through(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=None
    )
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert settings.mcp_config != {}
    assert set(settings.mcp_config.keys()) == {"fetch", "other"}


def test_mcp_empty_refs_means_none(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=[]
    )
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert settings.mcp_config == {}


def test_mcp_filter_selects_named_keys(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["other"]
    )
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert settings.mcp_config != {}
    assert list(settings.mcp_config.keys()) == ["other"]


def test_mcp_dangling_ref_raises(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch", "missing"]
    )
    with pytest.raises(DanglingMcpServerRef) as exc:
        resolve_agent_profile(
            profile,
            llm_store=llm_store,
            mcp_config=mcp_config,
            available_skills=None,
            cipher=None,
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
            profile,
            llm_store=llm_store,
            mcp_config={},
            available_skills=None,
            cipher=None,
        )
    assert exc.value.missing == ["fetch"]


# --------------------------------------------------------------------------- #
# ACP path
# --------------------------------------------------------------------------- #


def test_acp_resolves_to_settings_without_credentials(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
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
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, ACPAgentSettings)
    assert settings.acp_server == "codex"
    assert settings.acp_model == "gpt-5.5/medium"
    assert settings.acp_session_mode == "full-access"
    # str command is tokenized into the settings' list[str] field.
    assert settings.acp_command == ["codex-acp", "--foo"]
    assert settings.acp_args == ["--flag"]
    assert settings.mcp_config != {}
    assert list(settings.mcp_config.keys()) == ["fetch"]
    # No credential is injected; the deprecated llm channel stays empty.
    assert settings.llm.api_key is None
    assert isinstance(settings.create_agent(), ACPAgent)


def test_acp_blank_command_resolves_empty_list(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
    )
    assert isinstance(settings, ACPAgentSettings)
    assert settings.acp_command == []
    assert settings.acp_args == []


def test_acp_carries_no_user_public_skills_but_keeps_load_project_skills(
    llm_store: LLMProfileStore,
) -> None:
    """ACP profiles inject no user/public discovered skills (the subprocess owns
    its context), but ``agent_context`` is still constructed (never ``None``) so
    ``load_project_skills=True`` reaches ``LocalConversation``'s lazy
    project-skill load (#4016). ACP convention: no injected datetime."""
    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert isinstance(settings, ACPAgentSettings)
    assert settings.agent_context is not None
    assert settings.agent_context.skills == []
    assert settings.agent_context.load_project_skills is True
    assert settings.agent_context.current_datetime is None


# --------------------------------------------------------------------------- #
# Dry-run
# --------------------------------------------------------------------------- #


def test_dry_run_openhands_valid_and_redacted(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch"]
    )
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert diag.agent_kind == "openhands"
    assert diag.valid is True
    assert diag.errors == []
    assert diag.llm_profile_ref == "default"
    assert diag.llm_profile_resolved is True
    assert diag.llm_api_key_set is True
    assert diag.resolved_mcp_config_keys == ["fetch"]
    assert diag.resolved_mcp_servers == ["fetch"]
    assert diag.dangling_mcp_server_refs == []
    assert diag.resolved_settings is not None
    # No secret survives into the redacted resolved settings.
    dumped = diag.model_dump_json()
    assert _LLM_SECRET not in dumped
    assert _MCP_SECRET not in dumped


def test_dry_run_reports_dangling_llm_and_mcp(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="nope", mcp_server_refs=["missing"]
    )
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert diag.valid is False
    assert diag.llm_profile_resolved is False
    assert diag.dangling_mcp_server_refs == ["missing"]
    assert len(diag.errors) == 2
    # Invalid => no resolved settings produced.
    assert diag.resolved_settings is None


def test_dry_run_reports_disabled_and_resolved_skills(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # Deny-list: the dry-run reports the disabled names and the resolved set
    # (catalog minus disabled). A disabled name absent from the catalog does not
    # invalidate the profile — the deny-list can't dangle.
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", disabled_skills=["beta", "missing"]
    )
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert diag.disabled_skills == ["beta", "missing"]
    assert set(diag.resolved_skills) == {"alpha", "gamma"}
    assert diag.valid is True
    assert not any("Skill" in e for e in diag.errors)
    assert diag.resolved_settings is not None


def test_dry_run_default_disabled_resolves_all_discovered(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # Default deny-list ([]) resolves the whole catalog.
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert diag.disabled_skills == []
    assert set(diag.resolved_skills) == {"alpha", "beta", "gamma"}


def test_dry_run_total_on_llm_store_transient_error(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
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
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert diag.valid is False
    assert diag.llm_profile_resolved is False
    # Reported as "could not load" (transient), distinct from "not found".
    assert any("Could not load LLM profile" in e for e in diag.errors)
    assert diag.resolved_settings is None


def test_dry_run_verdict_matches_real_resolve(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # A dangling MCP ref: dry-run says invalid, real resolve raises.
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["missing"]
    )
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert diag.valid is False
    with pytest.raises(DanglingMcpServerRef):
        resolve_agent_profile(
            profile,
            llm_store=llm_store,
            mcp_config=mcp_config,
            available_skills=None,
            cipher=None,
        )


def test_dry_run_acp_reports_credential_channels_by_role(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp", acp_server="codex")
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
    )
    assert diag.agent_kind == "acp"
    assert diag.valid is True
    # The API key and the file-content credential are alternative auth paths;
    # the base URL is optional proxy routing — each surfaced in its own field.
    assert diag.acp_api_key_secret_name == "OPENAI_API_KEY"
    assert diag.acp_base_url_secret_name == "OPENAI_BASE_URL"
    assert diag.acp_file_secret_names == ["CODEX_AUTH_JSON"]
    assert diag.resolved_settings is not None


def test_dry_run_acp_reports_no_skills(
    llm_store: LLMProfileStore,
) -> None:
    # ACP profiles carry no skill field, so the dry-run reports no resolved
    # skills regardless of the catalog, and stays valid.
    profile = ACPAgentProfile(name="acp", acp_server="codex")
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert diag.disabled_skills == []
    assert diag.resolved_skills == []
    assert diag.valid is True


def test_dry_run_skill_verdict_matches_real_resolve(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # A disabled name absent from the catalog never invalidates: dry-run stays
    # valid and the real resolve succeeds with the full catalog. The deny-list
    # cannot dangle (unlike the MCP allow-list).
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", disabled_skills=["missing"]
    )
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert diag.valid is True
    settings = resolve_agent_profile(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=_discovered_skills(),
        cipher=None,
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert {s.name for s in settings.agent_context.skills} == {
        "alpha",
        "beta",
        "gamma",
    }


def test_dry_run_unknown_catalog_resolves_no_skills(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # available_skills=None (discovery skipped/failed) → no user/public skills
    # resolve, and the profile stays valid (the deny-list has nothing to flag).
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", disabled_skills=["alpha"]
    )
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        available_skills=None,
        cipher=None,
    )
    assert diag.resolved_skills == []
    assert diag.valid is True


def test_dry_run_acp_custom_server_has_no_credential_channels(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(
        name="acp", acp_server="custom", acp_command="my-acp-server"
    )
    diag = resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
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
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
    )
    assert diag.valid is False
    assert diag.errors
    assert diag.resolved_settings is None
    with pytest.raises(ValueError):
        resolve_agent_profile(
            profile,
            llm_store=llm_store,
            mcp_config={},
            available_skills=None,
            cipher=None,
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
        profile,
        llm_store=llm_store,
        mcp_config={},
        available_skills=None,
        cipher=None,
    )
    assert diag.valid is False
    assert diag.errors
    assert diag.resolved_settings is None
