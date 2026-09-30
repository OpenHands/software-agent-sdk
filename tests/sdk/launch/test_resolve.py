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
    assert resolved.allowed_secrets == frozenset({"GITHUB_TOKEN"})


def test_an_unknown_profile_id_is_not_found():
    with pytest.raises(ProfileNotFound):
        resolve(uuid4(), fakes.stores())


def test_every_dangling_reference_is_reported_together():
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="missing-llm",
        mcp_server_refs=["present", "missing-mcp"],
    )

    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        resolve(profile, fakes.stores(mcp=_mcp("present")))

    detail = exc_info.value.to_detail()
    assert detail["code"] == "unresolved_profile_references"
    assert detail["dangling_llm_profile_ref"] == "missing-llm"
    assert detail["dangling_mcp_server_refs"] == ["missing-mcp"]


def test_the_profile_llm_streams_without_changing_the_stored_llm():
    stored_llm = fakes.llm(stream=False, api_key=SecretStr("sk"))

    resolved = resolve(_profile(), fakes.stores(llms={"default": stored_llm}))

    settings = _openhands(resolved)
    assert settings.llm.stream is True
    assert stored_llm.stream is False


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
