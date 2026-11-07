"""Routes for user-facing feature flags (see :mod:`openhands.agent_server.features`)."""

import asyncio

from fastapi import APIRouter, HTTPException, Request, status

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.features import (
    FEATURES_BY_KEY,
    FeatureFlag,
    FeatureFlagUpdate,
    feature_flags,
)
from openhands.agent_server.persistence import PersistedSettings, get_settings_store


features_router = APIRouter(prefix="/settings/features", tags=["Settings"])


@features_router.get("", response_model=list[FeatureFlag])
async def list_features(request: Request) -> list[FeatureFlag]:
    """List the features the user can turn on or off, and whether each is on."""
    store = get_settings_store(get_config(request))
    settings = await asyncio.to_thread(lambda: store.load() or PersistedSettings())
    return feature_flags(settings.features)


@features_router.patch(
    "/{key}",
    response_model=FeatureFlag,
    responses={404: {"description": "There is no feature with this key"}},
)
def update_feature(
    request: Request, key: str, payload: FeatureFlagUpdate
) -> FeatureFlag:
    """Turn a feature on or off.

    The change applies to conversations started after it; running ones keep
    what they started with.
    """
    if key not in FEATURES_BY_KEY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown feature: {key}"
        )

    def apply(settings: PersistedSettings) -> PersistedSettings:
        settings.features = {**settings.features, key: payload.enabled}
        return settings

    store = get_settings_store(get_config(request))
    try:
        settings = store.update(apply)
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settings file is corrupted or encrypted with a different key",
        )
    return next(f for f in feature_flags(settings.features) if f.key == key)
