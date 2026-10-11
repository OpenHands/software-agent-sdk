from pydantic import SecretStr

from openhands.sdk.launch import LaunchRuntime, preview_launch
from openhands.sdk.profiles.agent_profile import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from tests.sdk.launch import fakes


CATALOG = [Skill(name="alpha", content="a"), Skill(name="beta", content="b")]


def test_a_dangling_mcp_ref_still_reports_the_llm_and_skills():
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="default",
        mcp_server_refs=["missing"],
        disabled_skills=["beta"],
    )
    stores = fakes.stores(
        llms={"default": fakes.llm(api_key=SecretStr("sk"))}, skills=CATALOG
    )

    report = preview_launch(profile, stores, LaunchRuntime())

    assert report.valid is False
    assert report.dangling_mcp_server_refs == ["missing"]
    assert report.llm_profile_resolved is True
    assert report.llm_api_key_set is True
    assert report.resolved_skills == ["alpha"]


def test_resolved_settings_match_what_the_launch_builds():
    profile = ACPAgentProfile(name="a", acp_server="claude-code")

    report = preview_launch(
        profile,
        fakes.stores(skills=CATALOG),
        LaunchRuntime(acp_skill_sourcing="native"),
        load_memory=True,
    )

    assert report.valid is True
    assert report.resolved_settings is not None
    context = report.resolved_settings["agent_context"]
    assert report.resolved_skills == context["skills"] == []
    assert context["load_memory"] is True


def test_settings_that_cannot_build_an_agent_are_reported(monkeypatch):
    def fail(self):
        raise ValueError("OpenAI subscription login is required")

    monkeypatch.setattr(OpenHandsAgentSettings, "create_agent", fail)

    report = preview_launch(
        OpenHandsAgentProfile(name="p", llm_profile_ref="default"),
        fakes.stores(),
        LaunchRuntime(),
    )

    assert report.valid is False
    assert any("subscription login" in error for error in report.errors)
