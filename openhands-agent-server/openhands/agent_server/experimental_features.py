"""User-facing experimental features (feature flags).

A feature is a capability the user turns on or off for themselves. Each one is
a yes/no switch stored in ``PersistedSettings.experimental_features`` and off
until the user turns it on. Unknown keys are refused, so
``EXPERIMENTAL_FEATURES`` is the whole set.

To add a feature, append an :class:`ExperimentalFeatureDefinition` to
``EXPERIMENTAL_FEATURES`` and read it where it takes effect with
:func:`is_enabled`.

The tools a feature gives the agent are chosen when a conversation starts
(:func:`experimental_feature_tools`), so they follow the switch for
conversations started after it; a route that checks the switch follows it at
once. Child conversations reuse their parent's agent and tools.

A Docker runtime's server has its own, empty settings: the host passes it the
user's switches in ``OH_RUNTIME_EXPERIMENTAL_FEATURES`` and checks the live
ones itself before forwarding a request (see :func:`host_experimental_features`).
"""

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from openhands.sdk.tool.spec import Tool


if TYPE_CHECKING:
    from openhands.agent_server.persistence import SettingsStore


@dataclass(frozen=True)
class ExperimentalFeatureDefinition:
    key: str
    label: str
    description: str


class ExperimentalFeature(BaseModel):
    """A feature and whether it is on for this user."""

    key: str
    label: str
    description: str
    enabled: bool


class ExperimentalFeatureUpdate(BaseModel):
    enabled: bool


CONVERSATION_REACTIONS = ExperimentalFeatureDefinition(
    key="conversation_reactions",
    label="Conversation reactions",
    description=(
        "The agent can put an emoji reaction on your messages, for example 👀 "
        "when it starts on something before it answers, and you can react to "
        "its messages. Applies to conversations started after you turn it on."
    ),
)

PROJECTS = ExperimentalFeatureDefinition(
    key="projects",
    label="Projects",
    description=(
        "Group conversations into projects: a coordinator conversation hands "
        "work to other conversations, hears back from them, and keeps project "
        "files and memory. Turned off, the projects API is unavailable and new "
        "conversations get no project tools; existing projects are kept."
    ),
)

PROJECT_TOOL_NAMES: tuple[str, ...] = (
    "list_project_conversations",
    "message_project_conversation",
    "read_project_conversation",
    "write_artifact",
    "read_artifact",
    "list_artifacts",
    "read_project_memory",
    "update_project_memory",
    "resolve_conversation",
)
"""Tools registered by openhands.tools.project. A client lists the coordinator's
in its agent spec and the server adds the workers', so they are withheld from
new conversations while Projects is off rather than added while it is on."""

EXPERIMENTAL_FEATURES: list[ExperimentalFeatureDefinition] = [
    CONVERSATION_REACTIONS,
    PROJECTS,
]


def find_experimental_feature(key: str) -> ExperimentalFeatureDefinition | None:
    return next((f for f in EXPERIMENTAL_FEATURES if f.key == key), None)


def is_enabled(features: Mapping[str, bool], key: str) -> bool:
    return features.get(key, False)


def experimental_feature_flags(
    features: Mapping[str, bool],
) -> list[ExperimentalFeature]:
    return [
        ExperimentalFeature(
            key=f.key,
            label=f.label,
            description=f.description,
            enabled=is_enabled(features, f.key),
        )
        for f in EXPERIMENTAL_FEATURES
    ]


def experimental_feature_tools(features: Mapping[str, bool]) -> list[Tool]:
    """Tools to add to an OpenHands agent for the features that are on."""
    if is_enabled(features, CONVERSATION_REACTIONS.key):
        # Tool names as registered by openhands.tools.reaction.
        return [Tool(name="react"), Tool(name="unreact")]
    return []


def experimental_feature_withheld_tools(features: Mapping[str, bool]) -> list[str]:
    """Tools a new conversation does not get, even if its spec lists them."""
    return [] if is_enabled(features, PROJECTS.key) else list(PROJECT_TOOL_NAMES)


async def feature_enabled_now(key: str, settings_store: "SettingsStore | None") -> bool:
    """Whether the user has ``key`` on right now (host switches in a runtime).

    For server behavior that follows the switch at once, such as a route or a
    wake-up. An unreadable settings file, or no store, counts as off.
    """
    host_features = host_experimental_features()
    if host_features is not None:
        return is_enabled(host_features, key)
    if settings_store is None:
        return False
    from openhands.agent_server.persistence import PersistedSettings

    try:
        settings = await asyncio.to_thread(
            lambda: settings_store.load() or PersistedSettings()
        )
    except (PermissionError, OSError):
        return False
    return is_enabled(settings.experimental_features, key)


RUNTIME_EXPERIMENTAL_FEATURES_ENV = "OH_RUNTIME_EXPERIMENTAL_FEATURES"


def host_experimental_features() -> dict[str, bool] | None:
    """The host's switches, in a Docker runtime's server; None elsewhere.

    The host sets them when it starts the runtime's container, so they pick
    the tools of the conversations started there. A route that follows a
    switch at once is checked by the host before it forwards the request.
    """
    raw = os.getenv(RUNTIME_EXPERIMENTAL_FEATURES_ENV)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        key: value
        for key, value in parsed.items()
        if find_experimental_feature(key) is not None and isinstance(value, bool)
    }
