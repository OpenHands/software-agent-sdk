"""Routes for user-facing experimental features (see :mod:`.features`)."""

import asyncio

from fastapi import APIRouter, HTTPException, Request, status

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.experimental_features import (
    EXPERIMENTAL_FEATURES_BY_KEY,
    ExperimentalFeature,
    ExperimentalFeatureUpdate,
    experimental_feature_flags,
)
from openhands.agent_server.persistence import PersistedSettings, get_settings_store


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
    responses={404: {"description": "There is no feature with this key"}},
)
def update_experimental_feature(
    request: Request, key: str, payload: ExperimentalFeatureUpdate
) -> ExperimentalFeature:
    """Turn a feature on or off.

    The change applies to conversations started after it; running ones keep
    what they started with.
    """
    if key not in EXPERIMENTAL_FEATURES_BY_KEY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown experimental feature: {key}",
        )

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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settings file is corrupted or encrypted with a different key",
        )
    return next(
        f
        for f in experimental_feature_flags(settings.experimental_features)
        if f.key == key
    )
