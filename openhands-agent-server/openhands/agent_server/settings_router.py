from functools import lru_cache

from fastapi import APIRouter

from openhands.sdk.settings import (
    ConversationSettings,
    SettingsSchema,
    export_agent_settings_schema,
)


settings_router = APIRouter(prefix="/settings", tags=["Settings"])


@lru_cache(maxsize=1)
def _get_agent_settings_schema() -> SettingsSchema:
    # ``AgentSettings`` is now a discriminated union over
    # ``OpenHandsAgentSettings`` and ``ACPAgentSettings``; the combined
    # schema tags sections with a ``variant`` so the frontend can
    # show LLM-only or ACP-only sections based on the active
    # ``agent_kind`` value.
    return export_agent_settings_schema()


@lru_cache(maxsize=1)
def _get_conversation_settings_schema() -> SettingsSchema:
    return ConversationSettings.export_schema()


@settings_router.get("/agent-schema", response_model=SettingsSchema)
async def get_agent_settings_schema() -> SettingsSchema:
    """Return the schema used to render AgentSettings-based settings forms."""
    return _get_agent_settings_schema()


@settings_router.get("/conversation-schema", response_model=SettingsSchema)
async def get_conversation_settings_schema() -> SettingsSchema:
    """Return the schema used to render ConversationSettings-based forms."""
    return _get_conversation_settings_schema()
