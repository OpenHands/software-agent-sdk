"""User-facing experimental features (feature flags).

A feature is a capability the user turns on or off for themselves, such as
emoji reactions. Each one is a yes/no switch stored in
``PersistedSettings.experimental_features``. The tools a feature gives the
agent are chosen when a conversation starts, so they follow the switch for
conversations started after it; a route that checks the switch (such as the
person's reactions) follows it at once. Child conversations reuse their
parent's agent and tools. Unknown keys are refused, so the list below is the
whole set.

To add a feature, append a :class:`ExperimentalFeatureDefinition` to
``EXPERIMENTAL_FEATURES`` and read it where it takes effect with :func:`is_enabled`.

A Docker runtime's server has its own, empty settings: the host passes it the
person's switches in ``OH_RUNTIME_EXPERIMENTAL_FEATURES`` and checks the live
ones itself before forwarding a request (see :func:`host_experimental_features`).
"""

import json
import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from openhands.sdk.tool.spec import Tool


ExperimentalFeatureKey = Literal["conversation_reactions"]


class ExperimentalFeatureDefinition(BaseModel):
    key: ExperimentalFeatureKey
    label: str
    description: str
    default: bool = False
    tools: tuple[str, ...] = Field(
        default=(),
        description="Tools an OpenHands agent gets while the feature is on.",
    )


class ExperimentalFeature(BaseModel):
    """A feature and whether it is on for this user."""

    key: ExperimentalFeatureKey
    label: str
    description: str
    default: bool
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
    # Tool names as registered by openhands.tools.reaction.
    tools=("react", "unreact"),
)

EXPERIMENTAL_FEATURES: tuple[ExperimentalFeatureDefinition, ...] = (
    CONVERSATION_REACTIONS,
)
EXPERIMENTAL_FEATURES_BY_KEY: dict[str, ExperimentalFeatureDefinition] = {
    f.key: f for f in EXPERIMENTAL_FEATURES
}


def is_enabled(features: Mapping[str, bool], key: ExperimentalFeatureKey) -> bool:
    stored = features.get(key)
    return EXPERIMENTAL_FEATURES_BY_KEY[key].default if stored is None else stored


def experimental_feature_flags(
    features: Mapping[str, bool],
) -> list[ExperimentalFeature]:
    return [
        ExperimentalFeature(
            key=f.key,
            label=f.label,
            description=f.description,
            default=f.default,
            enabled=is_enabled(features, f.key),
        )
        for f in EXPERIMENTAL_FEATURES
    ]


def experimental_feature_tools(features: Mapping[str, bool]) -> list[Tool]:
    """Tools to add to an OpenHands agent for the features that are on."""
    return [
        Tool(name=name)
        for f in EXPERIMENTAL_FEATURES
        if is_enabled(features, f.key)
        for name in f.tools
    ]


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
        if key in EXPERIMENTAL_FEATURES_BY_KEY and isinstance(value, bool)
    }
