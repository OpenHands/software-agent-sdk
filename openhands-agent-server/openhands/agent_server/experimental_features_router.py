"""Routes for user-facing experimental features (see :mod:`.experimental_features`)."""

import asyncio

from fastapi import APIRouter, Request

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.errors import error_responses
from openhands.agent_server.experimental_features import (
    ExperimentalFeature,
    ExperimentalFeatureUpdate,
    experimental_feature_flags,
    find_experimental_feature,
    is_enabled,
)
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk.errors import Conflict, NotFound


experimental_features_router = APIRouter(
    prefix="/settings/experimental", tags=["Settings"]
)


@experimental_features_router.get("", response_model=list[ExperimentalFeature])
async def list_experimental_features(request: Request) -> list[ExperimentalFeature]:
    """List the features the user can turn on or off, and whether each is on."""
    store = get_settings_store(get_config(request))
    settings = await asyncio.to_thread(lambda: store.load() or PersistedSettings())
    return experimental_feature_flags(settings.experimental_features)


@experimental_features_router.patch(
    "/{key}",
    response_model=ExperimentalFeature,
    responses=error_responses(
        {
            NotFound: "There is no feature with this key",
            Conflict: "The settings file cannot be read",
        }
    ),
)
def update_experimental_feature(
    request: Request, key: str, payload: ExperimentalFeatureUpdate
) -> ExperimentalFeature:
    """Turn a feature on or off."""
    feature = find_experimental_feature(key)
    if feature is None:
        raise NotFound(f"Unknown experimental feature: {key}")

    def apply(settings: PersistedSettings) -> PersistedSettings:
        settings.experimental_features = {
            **settings.experimental_features,
            key: payload.enabled,
        }
        return settings

    store = get_settings_store(get_config(request))
    try:
        settings = store.update(apply)
    except RuntimeError:
        raise Conflict("Settings file is corrupted or encrypted with a different key")
    return ExperimentalFeature(
        key=feature.key,
        label=feature.label,
        description=feature.description,
        enabled=is_enabled(settings.experimental_features, key),
    )
