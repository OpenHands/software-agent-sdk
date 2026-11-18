"""The two predefined agent profiles of Projects: coordinator and worker.

A project's coordinator and its workers are specialized agents. Each one
runs a built-in system prompt made for its role (the
``system_prompt_project_coordinator.j2`` and ``system_prompt_project_worker.j2``
prompt filenames, assembled from sections like the planning prompt) and a tool
set to match. The server
creates a profile for each role the first time a project needs it, from the
user's active agent profile, so both keep the user's model, MCP servers,
secrets and skills. The user can edit them like any other profile; the
server never overwrites one that exists.

The role itself does not depend on these profiles: the server assigns the
prompt at launch (``AgentLaunchAdditions.system_prompt_filename``), so a coordinator
or worker started from any other profile still gets its role's prompt.
"""

from typing import Literal
from uuid import UUID, uuid4

from openhands.agent_server.persistence import (
    PersistedSettings,
    ServerStores,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.profiles.agent_profile_store import ProfileLimitExceeded
from openhands.sdk.tool.spec import Tool


logger = get_logger(__name__)

ProjectRole = Literal["coordinator", "worker"]

PROJECT_PROFILE_NAMES: dict[ProjectRole, str] = {
    "coordinator": "project-coordinator",
    "worker": "project-worker",
}
"""Names of the predefined profiles. The server looks them up by name, so a
user who renames one gets a fresh one on the next project."""

COORDINATOR_PROFILE_TOOLS = ("terminal", "file_editor", "task_tracker")
"""The coordinator's own tools, besides the project tools the server adds:
enough to look at a file or a repository when answering a quick question, and
no browser."""

WORKER_WITHHELD_TOOLS = ("start_child_conversation",)
"""Tools a worker profile drops from the user's: a worker's own workers would
report to no one yet."""


def _profile(role: ProjectRole, base: OpenHandsAgentProfile) -> OpenHandsAgentProfile:
    if role == "coordinator":
        tools: list[Tool] | None = [
            Tool(name=name) for name in COORDINATOR_PROFILE_TOOLS
        ]
    elif base.tools is None:
        tools = None
    else:
        tools = [t for t in base.tools if t.name not in WORKER_WITHHELD_TOOLS]
    return base.model_copy(
        update={
            "id": uuid4(),
            "name": PROJECT_PROFILE_NAMES[role],
            "revision": 0,
            "tools": tools,
            # The role's prompt carries the job; a persona the user wrote for
            # their everyday agent would replace parts of it.
            "persona": None,
        }
    )


def ensure_project_profile(role: ProjectRole, stores: ServerStores) -> UUID | None:
    """The id of the predefined profile for ``role``, creating it if missing.

    ``None`` when it cannot be made: no active OpenHands agent profile to build
    it from (an ACP agent builds its own prompt), or the profile store is full.
    Callers then use the user's active profile, and the role still comes
    from the launch.
    """
    from openhands.agent_server.agent_profiles_router import MAX_AGENT_PROFILES

    store = stores.agent_profiles
    name = PROJECT_PROFILE_NAMES[role]
    with store.lock():
        if f"{name}.json" in store.list():
            return store.load(name).id
        settings = stores.settings.load() or PersistedSettings()
        active_id = settings.active_agent_profile_id
        active_name = store.name_for_id(active_id) if active_id else None
        if active_name is None:
            return None
        base = store.load(active_name)
        if not isinstance(base, OpenHandsAgentProfile):
            return None
        profile = _profile(role, base)
        try:
            store.save(profile, max_profiles=MAX_AGENT_PROFILES)
        except ProfileLimitExceeded:
            logger.warning("No room for the %s agent profile", name)
            return None
    logger.info("Created the %s agent profile (id=%s)", name, profile.id)
    return profile.id


def runs_in_a_project(stores: ServerStores, profile_id: UUID) -> bool | None:
    """Whether a profile can run a project's coordinator or worker, ``None``
    when there is no such profile.

    An ACP agent cannot: it builds its own prompt and takes no server tools,
    so it would get neither the role's prompt nor the project tools.
    """
    store = stores.agent_profiles
    name = store.name_for_id(profile_id)
    if name is None:
        return None
    return not isinstance(store.load(name), ACPAgentProfile)
