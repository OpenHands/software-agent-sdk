"""User-facing experimental features (feature flags).

A feature is a capability the user turns on or off for themselves. Each one is
a yes/no switch stored in ``PersistedSettings.experimental_features`` and off
until the user turns it on. Unknown keys are refused, so
``EXPERIMENTAL_FEATURES`` is the whole set.

To add a feature, append an :class:`ExperimentalFeatureDefinition` to
``EXPERIMENTAL_FEATURES`` and read it where it takes effect with
:func:`is_enabled`.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel


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


EXPERIMENTAL_FEATURES: list[ExperimentalFeatureDefinition] = []


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
