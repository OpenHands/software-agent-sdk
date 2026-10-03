from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.sdk.launch import (
    AgentLaunchError,
    LaunchStoreError,
    LaunchStores,
    UnresolvedProfileReferences,
    resolve,
)
from openhands.sdk.llm.meta_profile_store import MetaProfile, MetaProfileClass
from openhands.sdk.llm.provider_connection_store import ProviderConnectionNotFound
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.profiles.resolver import ProfileNotFound
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from tests.sdk.launch import fakes


def _profile(**kwargs) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name=kwargs.pop("name", "p"), llm_profile_ref="default", **kwargs
    )


def _mcp(*names: str) -> dict[str, MCPServer]:
    return {name: MCPServer(command="echo", args=[name]) for name in names}


def _openhands(resolved) -> OpenHandsAgentSettings:
    assert isinstance(resolved.settings, OpenHandsAgentSettings)
    return resolved.settings


def test_a_stored_profile_resolves_with_its_provenance():
    profile = _profile(revision=4, secret_refs=["GITHUB_TOKEN"])

    resolved = resolve(profile.id, fakes.stores(profile))

    assert resolved.profile is not None
    assert resolved.profile.agent_profile_id == profile.id
    assert resolved.profile.revision == 4
    assert resolved.profile.secret_refs == ["GITHUB_TOKEN"]
    assert resolved.profile.inline is False
    assert resolved.allowed_secrets == frozenset({"GITHUB_TOKEN"})


def test_an_unknown_profile_id_is_not_found():
    with pytest.raises(ProfileNotFound):
        resolve(uuid4(), fakes.stores())


def test_an_inline_profile_is_recorded_as_inline_and_not_stored():
    draft = _profile(name="draft")
    stores = fakes.stores()

    resolved = resolve(draft, stores)

    assert resolved.profile is not None
    assert resolved.profile.inline is True
    assert resolved.profile.agent_profile_id == draft.id
    assert stores.agent_profiles is not None
    assert stores.agent_profiles.name_for_id(draft.id) is None


def test_every_dangling_reference_is_reported_together():
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="missing-llm",
        mcp_server_refs=["present", "missing-mcp"],
        enable_classify_and_switch_llm_tool=True,
        meta_profile_ref="missing-meta",
    )

    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        resolve(profile, fakes.stores(mcp=_mcp("present")))

    detail = exc_info.value.to_detail()
    assert detail["code"] == "unresolved_profile_references"
    assert detail["dangling_llm_profile_ref"] == "missing-llm"
    assert detail["dangling_mcp_server_refs"] == ["missing-mcp"]
    assert detail["dangling_meta_profile_ref"] == "missing-meta"


def test_the_profile_llm_streams_without_changing_the_stored_llm():
    stored_llm = fakes.llm(stream=False, api_key=SecretStr("sk"))

    resolved = resolve(_profile(), fakes.stores(llms={"default": stored_llm}))

    settings = _openhands(resolved)
    assert settings.llm.stream is True
    assert stored_llm.stream is False


def test_a_per_launch_llm_override_is_used_and_recorded():
    llms = {"default": fakes.llm("default-model"), "fast": fakes.llm("fast-model")}

    resolved = resolve(_profile(), fakes.stores(llms=llms), llm_profile_ref="fast")

    assert _openhands(resolved).llm.model == "fast-model"
    assert resolved.profile is not None
    assert resolved.profile.llm_profile_ref == "fast"


def test_an_llm_override_for_an_acp_profile_is_rejected():
    with pytest.raises(AgentLaunchError, match="OpenHands"):
        resolve(ACPAgentProfile(name="a"), fakes.stores(), llm_profile_ref="fast")


@pytest.mark.parametrize(
    ("refs", "expected"),
    [(None, ["a", "b"]), ([], []), (["b"], ["b"])],
)
def test_mcp_server_refs_filter_the_user_servers(refs, expected):
    resolved = resolve(_profile(mcp_server_refs=refs), fakes.stores(mcp=_mcp("a", "b")))

    assert list(resolved.settings.mcp_config) == expected


def test_an_openhands_profile_gets_the_catalog_minus_its_deny_list():
    catalog = [Skill(name="alpha", content="a"), Skill(name="beta", content="b")]

    resolved = resolve(
        _profile(disabled_skills=["beta", "absent"]), fakes.stores(skills=catalog)
    )

    context = _openhands(resolved).agent_context
    assert [s.name for s in context.skills] == ["alpha"]
    assert context.disabled_skills == ["beta", "absent"]
    assert context.load_project_skills is True


def test_an_acp_profile_gets_the_whole_catalog_whatever_the_runtime():
    catalog = [Skill(name="alpha", content="a")]

    resolved = resolve(ACPAgentProfile(name="a"), fakes.stores(skills=catalog))

    assert isinstance(resolved.settings, ACPAgentSettings)
    context = resolved.settings.agent_context
    assert context is not None
    assert [s.name for s in context.skills] == ["alpha"]
    assert context.current_datetime is None
    assert context.load_project_skills is False


def test_a_meta_profile_and_the_llms_it_routes_to_are_copied_in():
    meta = MetaProfile(
        classifier_model="classifier",
        classes=[MetaProfileClass(description="ui", model="vision")],
    )
    llms = {
        "default": fakes.llm(),
        "classifier": fakes.llm("classifier-model"),
        "vision": fakes.llm("vision-model"),
    }
    profile = _profile(
        enable_classify_and_switch_llm_tool=True, meta_profile_ref="router"
    )

    settings = _openhands(
        resolve(profile, fakes.stores(llms=llms, metas={"router": meta}))
    )

    assert settings.enable_classify_and_switch_llm_tool is True
    assert settings.active_meta_profile == "router"
    assert settings.meta_profile == meta
    assert {name: llm.model for name, llm in settings.meta_profile_llms.items()} == {
        "classifier": "classifier-model",
        "vision": "vision-model",
    }


def test_a_meta_profile_routing_to_a_missing_llm_fails_the_launch():
    meta = MetaProfile(
        classifier_model="classifier",
        classes=[MetaProfileClass(description="ui", model="missing")],
    )
    llms = {"default": fakes.llm(), "classifier": fakes.llm()}
    profile = _profile(
        enable_classify_and_switch_llm_tool=True, meta_profile_ref="router"
    )

    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        resolve(profile, fakes.stores(llms=llms, metas={"router": meta}))

    assert exc_info.value.meta_profile_llm_refs == ["missing"]


def test_direct_routing_copies_every_saved_llm():
    meta = MetaProfile(
        classifier_model="classifier",
        prompt_template="Pick a model for {{ instance_text }}",
    )
    llms = {"default": fakes.llm(), "classifier": fakes.llm(), "other": fakes.llm()}
    profile = _profile(
        enable_classify_and_switch_llm_tool=True, meta_profile_ref="router"
    )

    settings = _openhands(
        resolve(profile, fakes.stores(llms=llms, metas={"router": meta}))
    )

    assert set(settings.meta_profile_llms) == {"default", "classifier", "other"}


class _OrphanedLLMs(fakes.LLMProfiles):
    def load(self, name, *, cipher=None):
        if name == "orphaned":
            raise ProviderConnectionNotFound("Provider connection 'gone' not found")
        return super().load(name, cipher=cipher)


def _direct_routing_stores(llm_profiles, names) -> LaunchStores:
    meta = MetaProfile(
        classifier_model="classifier",
        prompt_template="Pick a model for {{ instance_text }}",
    )
    return LaunchStores(
        llm_profiles=llm_profiles,
        llm_profile_names=names,
        mcp_config={},
        skills=lambda: [],
        meta_profiles=fakes.MetaProfiles({"router": meta}),
    )


def test_an_llm_profile_whose_provider_connection_is_gone_says_so():
    stores = LaunchStores(
        llm_profiles=_OrphanedLLMs({}), mcp_config={}, skills=lambda: []
    )
    profile = OpenHandsAgentProfile(name="p", llm_profile_ref="orphaned")

    with pytest.raises(AgentLaunchError, match="Provider connection 'gone'"):
        resolve(profile, stores)


def test_direct_routing_skips_a_saved_llm_it_cannot_load():
    llms = _OrphanedLLMs({"default": fakes.llm(), "classifier": fakes.llm()})
    stores = _direct_routing_stores(llms, lambda: ["default", "classifier", "orphaned"])
    profile = _profile(
        enable_classify_and_switch_llm_tool=True, meta_profile_ref="router"
    )

    settings = _openhands(resolve(profile, stores))

    assert set(settings.meta_profile_llms) == {"default", "classifier"}


def test_a_busy_store_listing_the_llm_profiles_is_retryable():
    def busy() -> list[str]:
        raise TimeoutError("locked")

    llms = fakes.LLMProfiles({"default": fakes.llm(), "classifier": fakes.llm()})
    profile = _profile(
        enable_classify_and_switch_llm_tool=True, meta_profile_ref="router"
    )

    with pytest.raises(LaunchStoreError) as exc_info:
        resolve(profile, _direct_routing_stores(llms, busy))

    assert exc_info.value.retryable is True


def test_a_disabled_routing_tool_ignores_its_meta_profile_ref():
    profile = _profile(meta_profile_ref="missing")

    settings = _openhands(resolve(profile, fakes.stores()))

    assert settings.enable_classify_and_switch_llm_tool is False
    assert settings.meta_profile is None


def _failing_llm_store(exc: Exception) -> LaunchStores:
    base = fakes.stores()

    class Failing:
        def load(self, name, *, cipher=None):
            raise exc

    return LaunchStores(
        llm_profiles=Failing(),
        mcp_config=base.mcp_config,
        skills=base.skills,
    )


@pytest.mark.parametrize(
    ("exc", "retryable"),
    [(TimeoutError("busy"), True), (ValueError("corrupt"), False)],
)
def test_a_store_that_cannot_be_read_is_not_a_dangling_reference(exc, retryable):
    with pytest.raises(LaunchStoreError) as exc_info:
        resolve(_profile(), _failing_llm_store(exc))

    assert exc_info.value.retryable is retryable


def test_a_failing_skill_discovery_is_a_store_error():
    base = fakes.stores()

    def broken() -> list[Skill]:
        raise RuntimeError("clone failed")

    stores = LaunchStores(llm_profiles=base.llm_profiles, mcp_config={}, skills=broken)
    with pytest.raises(LaunchStoreError, match="clone failed"):
        resolve(_profile(), stores)


def test_a_custom_acp_profile_without_a_command_is_rejected():
    with pytest.raises(AgentLaunchError, match="acp_command"):
        resolve(ACPAgentProfile(name="a", acp_server="custom"), fakes.stores())
