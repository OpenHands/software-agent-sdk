"""User-facing feature flags.

A feature is a capability the user turns on or off for themselves, such as
emoji reactions. Each one is a yes/no switch stored in
``PersistedSettings.features`` and read when a conversation starts, so a change
applies to conversations started after it. Unknown keys are refused, so the
list below is the whole set.

To add a feature, append a :class:`FeatureDefinition` to ``FEATURES`` and read
it where it takes effect with :func:`is_enabled`.
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from openhands.sdk.tool.spec import Tool


FeatureKey = Literal["conversation_reactions"]


class FeatureDefinition(BaseModel):
    key: FeatureKey
    label: str
    description: str
    default: bool = False
    tools: tuple[str, ...] = Field(
        default=(),
        description="Tools an OpenHands agent gets while the feature is on.",
    )


class FeatureFlag(BaseModel):
    """A feature and whether it is on for this user."""

    key: FeatureKey
    label: str
    description: str
    default: bool
    enabled: bool


class FeatureFlagUpdate(BaseModel):
    enabled: bool


CONVERSATION_REACTIONS = FeatureDefinition(
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

FEATURES: tuple[FeatureDefinition, ...] = (CONVERSATION_REACTIONS,)
FEATURES_BY_KEY: dict[str, FeatureDefinition] = {f.key: f for f in FEATURES}


def is_enabled(features: Mapping[str, bool], key: FeatureKey) -> bool:
    stored = features.get(key)
    return FEATURES_BY_KEY[key].default if stored is None else stored


def feature_flags(features: Mapping[str, bool]) -> list[FeatureFlag]:
    return [
        FeatureFlag(
            key=f.key,
            label=f.label,
            description=f.description,
            default=f.default,
            enabled=is_enabled(features, f.key),
        )
        for f in FEATURES
    ]


def feature_tools(features: Mapping[str, bool]) -> list[Tool]:
    """Tools to add to an OpenHands agent for the features that are on."""
    return [
        Tool(name=name)
        for f in FEATURES
        if is_enabled(features, f.key)
        for name in f.tools
    ]
