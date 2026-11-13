"""The predefined project-coordinator and project-worker agent profiles."""

import pytest

from openhands.agent_server.persistence import PersistedSettings, ServerStores
from openhands.agent_server.project_profiles import (
    COORDINATOR_PROFILE_TOOLS,
    PROJECT_PROFILE_NAMES,
    ensure_project_profile,
)
from openhands.sdk.context.prompts.presets import PromptPreset
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.tool import Tool


@pytest.fixture
def stores(server_stores) -> ServerStores:
    return server_stores


def _activate(stores: ServerStores, profile: OpenHandsAgentProfile) -> None:
    stores.agent_profiles.save(profile)
    store = stores.settings
    settings = store.load() or PersistedSettings()
    store.save(settings.model_copy(update={"active_agent_profile_id": profile.id}))


def _user_profile() -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name="mine",
        llm_profile_ref="strong-model",
        tools=[
            Tool(name="terminal"),
            Tool(name="file_editor"),
            Tool(name="start_child_conversation"),
        ],
        persona="You are a ramen chef.",
        mcp_server_refs=["github"],
        system_message_suffix="Cite file paths.",
    )


def test_without_an_active_profile_there_is_nothing_to_build_from(stores):
    assert ensure_project_profile("coordinator", stores) is None
    assert ensure_project_profile("worker", stores) is None


def test_the_profiles_are_built_from_the_users_active_profile(stores):
    _activate(stores, _user_profile())

    coordinator_id = ensure_project_profile("coordinator", stores)
    worker_id = ensure_project_profile("worker", stores)

    store = stores.agent_profiles
    coordinator = store.load(PROJECT_PROFILE_NAMES["coordinator"])
    worker = store.load(PROJECT_PROFILE_NAMES["worker"])
    assert isinstance(coordinator, OpenHandsAgentProfile)
    assert isinstance(worker, OpenHandsAgentProfile)
    assert (coordinator.id, worker.id) == (coordinator_id, worker_id)
    assert coordinator.prompt_preset == PromptPreset.PROJECT_COORDINATOR
    assert worker.prompt_preset == PromptPreset.PROJECT_WORKER
    # The user's model, servers and suffix carry over; their persona does not.
    for profile in (coordinator, worker):
        assert profile.llm_profile_ref == "strong-model"
        assert profile.mcp_server_refs == ["github"]
        assert profile.system_message_suffix == "Cite file paths."
        assert profile.persona is None
    assert [t.name for t in coordinator.tools or []] == list(COORDINATOR_PROFILE_TOOLS)
    # A worker's own workers would report to no one yet.
    assert [t.name for t in worker.tools or []] == ["terminal", "file_editor"]


def test_an_existing_profile_is_kept_as_the_user_edited_it(stores):
    _activate(stores, _user_profile())
    first = ensure_project_profile("worker", stores)
    store = stores.agent_profiles
    edited = store.load(PROJECT_PROFILE_NAMES["worker"]).model_copy(
        update={"llm_profile_ref": "cheap-model"}
    )
    store.save(edited)

    assert ensure_project_profile("worker", stores) == first
    kept = store.load(PROJECT_PROFILE_NAMES["worker"])
    assert isinstance(kept, OpenHandsAgentProfile)
    assert kept.llm_profile_ref == "cheap-model"


def test_a_user_on_the_standard_tool_set_keeps_it_for_workers(stores):
    _activate(stores, _user_profile().model_copy(update={"tools": None}))

    ensure_project_profile("worker", stores)

    worker = stores.agent_profiles.load(PROJECT_PROFILE_NAMES["worker"])
    assert isinstance(worker, OpenHandsAgentProfile)
    assert worker.tools is None
