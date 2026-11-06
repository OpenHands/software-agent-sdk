"""Parity tests for the legacy profile resolver, migrated to the launch API.

These adapt ``tests/sdk/profiles/test_resolver.py`` (removed with the legacy
``resolve_agent_profile`` / ``resolve_agent_profile_dry_run`` entry points in
1.60.0) onto ``openhands.sdk.launch.resolve`` / ``preview_launch``. Each test
keeps its profile and assertions; only the call mechanism changed.

Two legacy-only behaviours are not re-tested here: keeping unset tools unset
when no browser availability is known (``resolve`` now leaves ``tools`` unset
by design and ``finalize`` applies the runtime's browser choice), and the
resolve/dry-run self-parity checks (``preview_launch`` runs ``resolve`` +
``finalize``, so it cannot disagree with a launch by construction). Both are
covered by ``test_resolve.py`` / ``test_preview.py`` / ``test_finalize.py``.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import ACPAgent, Agent
from openhands.sdk.launch import (
    LaunchRuntime,
    LaunchStores,
    UnresolvedProfileReferences,
    finalize,
    preview_launch,
    resolve,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.profiles.resolver import AgentProfileDiagnostics
from openhands.sdk.settings.model import (
    ACPAgentSettings,
    OpenHandsAgentSettings,
    validate_agent_settings,
)
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool, registry


_LLM_SECRET = "sk-LLM-SECRET-SHOULD-NOT-LEAK"
_MCP_SECRET = "ghp_MCP_SECRET_SHOULD_NOT_LEAK"


class _Adapter:
    """Adapt a real ``LLMProfileStore`` + dict config to ``LaunchStores``."""

    def __init__(self, store, mcp_config, skills, public_names=None, critic=None):
        self.stores = LaunchStores(
            llm_profiles=store,
            mcp_config=mcp_config if mcp_config is not None else {},
            skills=lambda: list(skills) if skills is not None else [],
            public_skill_names=(
                None if public_names is None else lambda: list(public_names)
            ),
            critic_api_key=critic,
        )

    def settings(self, profile, *, browser_available=None):
        """Legacy resolve: settings after the runtime's browser choice.

        ``resolve`` keeps an explicit/default tool list untouched; the
        browser choice is applied by ``finalize`` (as the usability gate
        lives in ``preview_launch``, whose report withholds settings when a
        tool cannot run on this host).
        """
        resolved = resolve(profile, self.stores)
        if browser_available is None:
            return resolved.settings
        launched = finalize(
            resolved, LaunchRuntime(browser_available=browser_available)
        )
        agent = launched.agent
        tools = [Tool(name=t.name, params=t.params or {}) for t in agent.tools]
        return validate_agent_settings(
            {
                **resolved.settings.model_dump(mode="json"),
                "tools": [t.model_dump(mode="json") for t in tools],
            }
        )

    def acp_settings(self, profile, *, browser_available=None):
        """ACP settings with the injected catalog (never runtime-stripped)."""
        del browser_available
        report = preview_launch(
            profile,
            self.stores,
            LaunchRuntime(acp_skill_sourcing="openhands_managed"),
        )
        assert report.resolved_settings is not None, report.errors
        return validate_agent_settings(report.resolved_settings)

    def diag(self, profile, *, browser_available=None, check_usable=True):
        """Legacy dry-run report (managed ACP skills; no runtime stripping)."""
        runtime = LaunchRuntime(
            browser_available=False if browser_available is None else browser_available,
            acp_skill_sourcing="openhands_managed",
        )
        report = preview_launch(
            profile, self.stores, runtime, check_usable=check_usable
        )
        return _DiagView(report, browser_available)


class _DiagView:
    """Legacy dry-run field surface on a ``preview_launch`` report."""

    def __init__(self, report: AgentProfileDiagnostics, browser):
        self._report = report
        self._browser = browser

    def __getattr__(self, name):
        return getattr(self._report, name)

    @property
    def resolved_settings(self):
        if self._browser is not None:
            return None
        return self._report.resolved_settings


def _stores(llm_store, mcp_config=None, skills=None, public_names=None, critic=None):
    return _Adapter(llm_store, mcp_config, skills, public_names, critic)


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


def _discovered_skills() -> list[Skill]:
    return [
        Skill(name="alpha", content="a"),
        Skill(name="beta", content="b"),
        Skill(name="gamma", content="c"),
    ]


def test_openhands_resolves_to_settings_with_injected_llm(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh",
        llm_profile_ref="default",
        agent="CodeActAgent",
        system_message_suffix="be terse",
        tool_concurrency_limit=3,
        mcp_server_refs=["fetch"],
    )
    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent == "CodeActAgent"
    assert settings.tool_concurrency_limit == 3
    assert settings.agent_context is not None
    assert settings.agent_context.system_message_suffix == "be terse"
    # LLM injected with the concrete (decrypted) credential.
    assert isinstance(settings.llm.api_key, SecretStr)
    assert settings.llm.api_key.get_secret_value() == _LLM_SECRET
    # MCP filtered to the referenced key.
    assert settings.mcp_config != {}
    assert list(settings.mcp_config.keys()) == ["fetch"]
    agent = settings.create_agent()
    assert isinstance(agent, Agent)
    # Delegation is a tool the profile selects, not a switch on the side.
    assert [t.name for t in agent.tools] == ["terminal", "file_editor", "task_tracker"]
    assert "SwitchLLMTool" in agent.include_default_tools


@pytest.mark.parametrize(
    ("browser_available", "expected"),
    [
        (False, ["terminal", "file_editor", "task_tracker"]),
        (True, ["terminal", "file_editor", "task_tracker", "browser_tool_set"]),
    ],
)
def test_openhands_resolves_default_exec_tools(
    llm_store: LLMProfileStore, browser_available: bool, expected: list[str]
) -> None:
    """Unset ``tools`` resolves to the standard set, browser only where usable."""
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    assert profile.tools is None

    settings = _stores(llm_store, mcp_config={}, skills=None).settings(
        profile, browser_available=browser_available
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    # The default-tool expansion happens when the settings build an agent;
    # the launch path defers it to finalize (already applied above).
    assert [t.name for t in settings.tools or []] == expected


def test_openhands_profile_tools_selection_is_used_as_given(
    llm_store: LLMProfileStore,
) -> None:
    """An explicit profile ``tools`` list is used as given ([] = deliberately
    bare) and never gets the browser."""
    picked = OpenHandsAgentProfile(
        name="picked",
        llm_profile_ref="default",
        tools=[Tool(name="terminal", params={"username": "dev"})],
    )
    settings = _stores(llm_store, mcp_config={}, skills=None).settings(
        picked, browser_available=True
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.create_agent().tools == [
        Tool(name="terminal", params={"username": "dev"})
    ]

    bare = OpenHandsAgentProfile(name="bare", llm_profile_ref="default", tools=[])
    settings = _stores(llm_store, mcp_config={}, skills=None).settings(
        bare, browser_available=True
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.create_agent().tools == []


@pytest.mark.parametrize(
    ("browser_available", "expected"),
    [
        (True, ["terminal", "browser_tool_set"]),
        (False, ["terminal"]),
        (None, ["terminal", "browser_tool_set"]),
    ],
)
def test_openhands_explicit_browser_is_launched_only_where_it_can_run(
    llm_store: LLMProfileStore, browser_available: bool | None, expected: list[str]
) -> None:
    profile = OpenHandsAgentProfile(
        name="pinned",
        llm_profile_ref="default",
        tools=[Tool(name="terminal"), Tool(name="browser_tool_set")],
    )

    settings = _stores(llm_store, mcp_config={}, skills=None).settings(
        profile, browser_available=browser_available
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert [tool.name for tool in settings.tools or []] == expected


@pytest.mark.parametrize("mcp_server_refs", [None, [], ["fetch"]])
def test_openhands_profile_delegation_is_scoped_to_the_profile_tools(
    llm_store: LLMProfileStore,
    mcp_config: dict[str, MCPServer],
    mcp_server_refs: list[str] | None,
) -> None:
    profile = OpenHandsAgentProfile(
        name="delegating",
        llm_profile_ref="default",
        tools=[Tool(name="terminal"), Tool(name="task_tool_set")],
        mcp_server_refs=mcp_server_refs,
    )

    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.tools == [
        Tool(name="terminal"),
        Tool(name="task_tool_set", params={"sub_agent_scope": {"tools": True}}),
    ]
    assert settings.create_agent().tools == settings.tools
    assert profile.tools == [Tool(name="terminal"), Tool(name="task_tool_set")]


def test_openhands_default_tools_are_not_scoped(llm_store: LLMProfileStore) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=[]
    )

    settings = _stores(llm_store, mcp_config={}, skills=None).settings(
        profile, browser_available=False
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert all(not tool.params for tool in settings.tools or [])


@pytest.mark.parametrize(
    ("tools", "browser_available", "unusable", "failing"),
    [
        ([Tool(name="browser_tool_set")], False, ["browser_tool_set"], []),
        ([Tool(name="browser_tool_set")], True, [], []),
        (None, False, ["browser_tool_set"], []),
        (None, True, [], []),
        (
            [Tool(name="finish"), Tool(name="not_registered")],
            True,
            ["not_registered"],
            ["not_registered"],
        ),
    ],
)
def test_dry_run_reports_selected_tools_it_cannot_run(
    llm_store: LLMProfileStore,
    tools: list[Tool] | None,
    browser_available: bool | None,
    unusable: list[str],
    failing: list[str],
) -> None:
    profile = OpenHandsAgentProfile(
        name="pinned", llm_profile_ref="default", tools=tools
    )

    diagnostics = _stores(llm_store, mcp_config={}, skills=None).diag(
        profile, browser_available=browser_available
    )

    assert diagnostics.unusable_tools == unusable
    assert diagnostics.valid is (failing == [])
    assert all(name in " ".join(diagnostics.errors) for name in failing)


@pytest.mark.parametrize(("check_usable", "unusable"), [(True, ["hello"]), (False, [])])
def test_dry_run_reports_a_registered_tool_that_is_not_usable(
    llm_store: LLMProfileStore,
    monkeypatch: pytest.MonkeyPatch,
    check_usable: bool,
    unusable: list[str],
) -> None:
    monkeypatch.setitem(registry._REG, "hello", lambda _params, _state: [])
    monkeypatch.setitem(registry._USABILITY_REG, "hello", lambda: False)
    profile = OpenHandsAgentProfile(
        name="pinned", llm_profile_ref="default", tools=[Tool(name="hello")]
    )

    diagnostics = _stores(llm_store, mcp_config={}, skills=None).diag(
        profile, check_usable=check_usable
    )

    assert diagnostics.unusable_tools == unusable
    assert diagnostics.valid is (unusable == [])


def test_openhands_profile_persona_keeps_capability_guidance(
    llm_store: LLMProfileStore,
) -> None:
    profile = OpenHandsAgentProfile(
        name="explorer",
        llm_profile_ref="default",
        persona="You are a read-only code explorer.",
        system_message_suffix="Cite file paths.",
    )
    settings = _stores(llm_store, mcp_config={}, skills=None).settings(profile)
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.persona == "You are a read-only code explorer."
    static = settings.create_agent().static_system_message
    assert static.startswith("You are a read-only code explorer.")
    assert "<SECURITY_RISK_ASSESSMENT>" in static
    assert "<ROLE>" not in static
    assert "Cite file paths." in (settings.create_agent().dynamic_context or "")


def test_openhands_profile_without_persona_keeps_builtin_prompt(
    llm_store: LLMProfileStore,
) -> None:
    settings = _stores(llm_store, mcp_config={}, skills=None).settings(
        OpenHandsAgentProfile(name="plain", llm_profile_ref="default")
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.persona is None
    agent = settings.create_agent()
    assert agent.persona is None
    assert "<ROLE>" in agent.static_system_message


def test_openhands_copies_verification(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    profile.verification.critic_enabled = True
    profile.verification.critic_model_name = "critic-x"

    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
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
    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent_context is not None
    assert settings.agent_context.load_project_skills is True


def test_missing_llm_ref_raises_unresolved(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="does-not-exist")
    with pytest.raises(UnresolvedProfileReferences):
        _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)


def test_disabled_skills_empty_includes_all_discovered(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # The default deny-list ([]) keeps every discovered skill.
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    assert profile.disabled_skills == []
    settings = _stores(
        llm_store, mcp_config=mcp_config, skills=_discovered_skills()
    ).settings(profile)
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
    settings = _stores(
        llm_store, mcp_config=mcp_config, skills=_discovered_skills()
    ).settings(profile)
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
    settings = _stores(
        llm_store, mcp_config=mcp_config, skills=_discovered_skills()
    ).settings(profile)
    assert isinstance(settings, OpenHandsAgentSettings)
    assert {s.name for s in settings.agent_context.skills} == {
        "alpha",
        "beta",
        "gamma",
    }


def test_duplicate_named_catalog_is_deduped(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # A colliding catalog (e.g. the app-server's fuller catalog merging sources
    # whose names overlap) is de-duplicated by name, last-wins — resolution must
    # not trip AgentContext's duplicate-name validator.
    catalog = [
        Skill(name="alpha", content="first"),
        Skill(name="beta", content="b"),
        Skill(name="alpha", content="second"),
    ]
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", disabled_skills=["beta"]
    )
    settings = _stores(llm_store, mcp_config=mcp_config, skills=catalog).settings(
        profile
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    skills = {s.name: s.content for s in settings.agent_context.skills}
    assert skills == {"alpha": "second"}  # de-duped (last wins); beta disabled


def test_acp_profile_has_no_skill_selection_field(
    llm_store: LLMProfileStore,
) -> None:
    # An ACP profile selects no skills of its own: the catalog the caller passes
    # is taken whole, since there is no deny-list to apply.
    acp = ACPAgentProfile(name="acp", acp_server="claude-code")
    assert not hasattr(acp, "skill_refs")
    assert not hasattr(acp, "disabled_skills")
    acp_settings = _stores(
        llm_store, mcp_config={}, skills=_discovered_skills()
    ).acp_settings(acp)
    assert isinstance(acp_settings, ACPAgentSettings)
    assert acp_settings.agent_context is not None
    assert {s.name for s in acp_settings.agent_context.skills} == {
        "alpha",
        "beta",
        "gamma",
    }


def test_acp_profile_gets_no_skills_without_a_catalog(
    llm_store: LLMProfileStore,
) -> None:
    # available_skills=None is how a deployment says "the ACP CLI sources its
    # own skills" (#4019) — nothing is injected.
    acp_settings = _stores(llm_store, mcp_config={}, skills=None).acp_settings(
        ACPAgentProfile(name="acp", acp_server="claude-code")
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
    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent_context.skills == []


def test_seed_then_resolve_with_narrower_catalog_does_not_dangle(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    from openhands.sdk.profiles import build_seed_profile
    from openhands.sdk.settings.model import validate_agent_settings

    settings = validate_agent_settings(
        {
            "agent_kind": "openhands",
            "agent_context": {
                "skills": [{"name": "inline-only", "content": "x"}],
                "disabled_skills": ["beta", "missing"],
            },
        }
    )
    profile = build_seed_profile(settings, active_llm_profile="default")
    assert isinstance(profile, OpenHandsAgentProfile)
    assert profile.disabled_skills == ["beta", "missing"]

    resolved = _stores(
        llm_store, mcp_config=mcp_config, skills=_discovered_skills()
    ).settings(profile)
    assert isinstance(resolved, OpenHandsAgentSettings)
    assert {s.name for s in resolved.agent_context.skills} == {
        "alpha",
        "gamma",
    }


def test_mcp_null_refs_passes_config_through(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=None
    )
    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
    assert settings.mcp_config != {}
    assert set(settings.mcp_config.keys()) == {"fetch", "other"}


def test_mcp_empty_refs_means_none(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=[]
    )
    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
    assert settings.mcp_config == {}


def test_mcp_filter_selects_named_keys(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["other"]
    )
    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
    assert settings.mcp_config != {}
    assert list(settings.mcp_config.keys()) == ["other"]


def test_mcp_dangling_ref_raises(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch", "missing"]
    )
    with pytest.raises(UnresolvedProfileReferences) as exc:
        _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
    assert exc.value.mcp_server_refs == ["missing"]


def test_mcp_dangling_when_config_is_none(
    llm_store: LLMProfileStore,
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch"]
    )
    with pytest.raises(UnresolvedProfileReferences) as exc:
        _stores(llm_store, mcp_config={}, skills=None).settings(profile)
    assert exc.value.mcp_server_refs == ["fetch"]


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
    settings = _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)
    assert isinstance(settings, ACPAgentSettings)
    assert settings.acp_server == "codex"
    assert settings.acp_model == "gpt-5.5/medium"
    assert settings.acp_session_mode == "full-access"
    # str command is tokenized into the settings' list[str] field.
    assert settings.acp_command == ["codex-acp", "--foo"]
    assert settings.acp_args == ["--flag"]
    assert settings.mcp_config != {}
    assert list(settings.mcp_config.keys()) == ["fetch"]
    assert "llm" not in settings.model_dump()
    assert isinstance(settings.create_agent(), ACPAgent)


def test_acp_blank_command_resolves_empty_list(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    settings = _stores(llm_store, mcp_config={}, skills=None).settings(profile)
    assert isinstance(settings, ACPAgentSettings)
    assert settings.acp_command == []
    assert settings.acp_args == []


def test_acp_never_loads_project_skills(
    llm_store: LLMProfileStore,
) -> None:
    """An ACP CLI reads ``AGENTS.md`` and its own project skills from the session
    cwd, so loading them here would duplicate that content in the prompt
    (#4019). ACP convention: no injected datetime."""
    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    settings = _stores(
        llm_store, mcp_config={}, skills=_discovered_skills()
    ).acp_settings(profile)
    assert isinstance(settings, ACPAgentSettings)
    assert settings.agent_context is not None
    assert settings.agent_context.load_project_skills is False
    assert settings.agent_context.current_datetime is None


def test_dry_run_openhands_valid_and_redacted(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["fetch"]
    )
    diag = _stores(llm_store, mcp_config=mcp_config, skills=None).diag(profile)
    assert diag.agent_kind == "openhands"
    assert diag.valid is True
    assert diag.errors == []
    assert diag.llm_profile_ref == "default"
    assert diag.llm_profile_resolved is True
    assert diag.llm_api_key_set is True
    assert diag.resolved_mcp_config_keys == ["fetch"]
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
    diag = _stores(llm_store, mcp_config=mcp_config, skills=None).diag(profile)
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
    diag = _stores(llm_store, mcp_config=mcp_config, skills=_discovered_skills()).diag(
        profile
    )
    assert diag.disabled_skills == ["beta", "missing"]
    assert set(diag.resolved_skills) == {"alpha", "gamma"}
    assert diag.valid is True
    assert not any("Skill" in e for e in diag.errors)
    assert diag.resolved_settings is not None


def test_resolve_and_dry_run_apply_public_skills_to_the_public_tier(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh",
        llm_profile_ref="default",
        public_skills=["alpha"],
        disabled_skills=["gamma"],
    )
    catalog = [*_discovered_skills(), Skill(name="mine", content="m")]
    public = {"alpha", "beta", "gamma"}

    settings = _stores(
        llm_store, mcp_config=mcp_config, skills=catalog, public_names=public
    ).settings(profile)
    diag = _stores(
        llm_store, mcp_config=mcp_config, skills=catalog, public_names=public
    ).diag(profile)

    assert isinstance(settings, OpenHandsAgentSettings)
    assert [s.name for s in settings.agent_context.skills] == ["alpha", "mine"]
    assert diag.public_skills == ["alpha"]
    assert diag.resolved_skills == ["alpha", "mine"]


def test_resolve_uses_the_server_critic_key(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")

    settings = _stores(
        llm_store, mcp_config=mcp_config, skills=[], critic=SecretStr("sk-critic")
    ).settings(profile)

    assert isinstance(settings, OpenHandsAgentSettings)
    key = settings.verification.critic_api_key
    assert isinstance(key, SecretStr) and key.get_secret_value() == "sk-critic"


def test_dry_run_default_disabled_resolves_all_discovered(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # Default deny-list ([]) resolves the whole catalog.
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    diag = _stores(llm_store, mcp_config=mcp_config, skills=_discovered_skills()).diag(
        profile
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
    diag = _stores(llm_store, mcp_config=mcp_config, skills=None).diag(profile)
    assert diag.valid is False
    assert diag.llm_profile_resolved is False
    # Reported as "could not load" (transient), distinct from "not found".
    assert any(
        "LLM profile store is busy" in e or "Could not load" in e for e in diag.errors
    )
    assert diag.resolved_settings is None


def test_dry_run_verdict_matches_real_resolve(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # A dangling MCP ref: dry-run says invalid, real resolve raises.
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", mcp_server_refs=["missing"]
    )
    diag = _stores(llm_store, mcp_config=mcp_config, skills=None).diag(profile)
    assert diag.valid is False
    with pytest.raises(UnresolvedProfileReferences):
        _stores(llm_store, mcp_config=mcp_config, skills=None).settings(profile)


def test_dry_run_acp_reports_credential_channels_by_role(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp", acp_server="codex")
    diag = _stores(llm_store, mcp_config={}, skills=None).diag(profile)
    assert diag.agent_kind == "acp"
    assert diag.valid is True
    # The API key and the file-content credential are alternative auth paths;
    # the base URL is optional proxy routing — each surfaced in its own field.
    assert diag.acp_api_key_secret_name == "OPENAI_API_KEY"
    assert diag.acp_base_url_secret_name == "OPENAI_BASE_URL"
    assert diag.acp_file_secret_names == ["CODEX_AUTH_JSON"]
    assert diag.resolved_settings is not None


def test_dry_run_acp_pi_reports_its_credential_file(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp-pi", acp_server="pi")
    diag = _stores(llm_store, mcp_config={}, skills=None).diag(profile)
    assert diag.agent_kind == "acp"
    assert diag.valid is True
    assert diag.acp_file_secret_names == ["PI_AUTH_JSON"]


def test_dry_run_acp_reports_the_injected_catalog(
    llm_store: LLMProfileStore,
) -> None:
    # An ACP profile has no deny-list, so the dry-run reports whatever catalog
    # the caller injected — and none when the caller injects nothing.
    profile = ACPAgentProfile(name="acp", acp_server="codex")
    diag = _stores(llm_store, mcp_config={}, skills=_discovered_skills()).diag(profile)
    assert diag.disabled_skills == []
    assert set(diag.resolved_skills) == {"alpha", "beta", "gamma"}
    assert diag.valid is True

    bare = _stores(llm_store, mcp_config={}, skills=None).diag(profile)
    assert bare.resolved_skills == []
    assert bare.valid is True


def test_dry_run_skill_verdict_matches_real_resolve(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    # A disabled name absent from the catalog never invalidates: dry-run stays
    # valid and the real resolve succeeds with the full catalog. The deny-list
    # cannot dangle (unlike the MCP allow-list).
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="default", disabled_skills=["missing"]
    )
    diag = _stores(llm_store, mcp_config=mcp_config, skills=_discovered_skills()).diag(
        profile
    )
    assert diag.valid is True
    settings = _stores(
        llm_store, mcp_config=mcp_config, skills=_discovered_skills()
    ).settings(profile)
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
    diag = _stores(llm_store, mcp_config=mcp_config, skills=None).diag(profile)
    assert diag.resolved_skills == []
    assert diag.valid is True


def test_dry_run_acp_custom_server_has_no_credential_channels(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(
        name="acp", acp_server="custom", acp_command="my-acp-server"
    )
    diag = _stores(llm_store, mcp_config={}, skills=None).diag(profile)
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
    diag = _stores(llm_store, mcp_config={}, skills=None).diag(profile)
    assert diag.valid is False
    assert diag.errors
    assert diag.resolved_settings is None
    with pytest.raises(ValueError):
        _stores(llm_store, mcp_config={}, skills=None).settings(profile)


def test_dry_run_normalizes_settings_build_failure(
    llm_store: LLMProfileStore,
) -> None:
    # An unbalanced-quote acp_command passes profile validation but breaks
    # shlex.split during settings construction; the dry-run must report it as
    # invalid rather than raising (its contract is total).
    profile = ACPAgentProfile(
        name="acp", acp_server="custom", acp_command="unterminated 'quote"
    )
    diag = _stores(llm_store, mcp_config={}, skills=None).diag(profile)
    assert diag.valid is False
    assert diag.errors
    assert diag.resolved_settings is None
