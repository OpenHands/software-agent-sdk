from functools import lru_cache

from fastapi import APIRouter

from openhands.sdk.settings import AgentSettings, SettingsSchema


settings_router = APIRouter(prefix="/settings", tags=["Settings"])


@lru_cache(maxsize=1)
def _get_agent_settings_schema() -> SettingsSchema:
    return AgentSettings.export_schema()


@settings_router.get("/schema", response_model=SettingsSchema)
async def get_agent_settings_schema() -> SettingsSchema:
    """Return the schema used to render AgentSettings-based settings forms."""
    return _get_agent_settings_schema()
