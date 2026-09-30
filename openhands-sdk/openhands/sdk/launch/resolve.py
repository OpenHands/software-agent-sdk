from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from openhands.sdk.launch.errors import (
    AgentLaunchError,
    LaunchStoreError,
    UnresolvedProfileReferences,
)
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    LaunchedAgentProfile,
    OpenHandsAgentProfile,
)
from openhands.sdk.profiles.resolver import (
    ProfileNotFound,
    _apply_disabled_skills,
    _build_acp_settings,
    _build_openhands_settings,
    _compute_mcp_filter,
)
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.skills import Skill


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.llm.llm_profile_store import LLMProfileLoader
    from openhands.sdk.utils.cipher import Cipher


class AgentProfileLoader(Protocol):
    def name_for_id(self, profile_id: str | UUID) -> str | None: ...

    def load(self, name: str) -> OpenHandsAgentProfile | ACPAgentProfile: ...


@dataclass(frozen=True, kw_only=True)
class LaunchStores:
    """The stores a launch reads, as seen by the process that holds them."""

    llm_profiles: LLMProfileLoader
    mcp_config: Mapping[str, MCPServer]
    skills: Callable[[], Sequence[Skill]]
    agent_profiles: AgentProfileLoader | None = None
    cipher: Cipher | None = None


@dataclass(frozen=True)
class ResolvedLaunch:
    """Reference-free agent settings plus the provenance of the launch."""

    settings: OpenHandsAgentSettings | ACPAgentSettings
    profile: LaunchedAgentProfile | None = None

    @property
    def allowed_secrets(self) -> frozenset[str] | None:
        if self.profile is None or self.profile.secret_refs is None:
            return None
        return frozenset(self.profile.secret_refs)


AgentProfileSource = UUID | OpenHandsAgentProfile | ACPAgentProfile


def resolve(source: AgentProfileSource, stores: LaunchStores) -> ResolvedLaunch:
    """Resolve a stored profile id or a profile into a ``ResolvedLaunch``.

    Every reference is copied into the result, so the runtime that finalizes it
    needs no store. All dangling references are raised together as
    :class:`UnresolvedProfileReferences`; a store that cannot be read raises
    :class:`LaunchStoreError`; an unknown profile id raises ``ProfileNotFound``.
    """
    profile = (
        _load_stored_profile(source, stores) if isinstance(source, UUID) else source
    )
    mcp_config, _, dangling_mcp = _compute_mcp_filter(
        dict(stores.mcp_config), profile.mcp_server_refs
    )
    catalog = _load_skills(stores)
    if isinstance(profile, ACPAgentProfile):
        if dangling_mcp:
            raise UnresolvedProfileReferences(mcp_server_refs=dangling_mcp)
        try:
            settings = _build_acp_settings(
                profile, mcp_config, _apply_disabled_skills(catalog, [])
            )
        except ValueError as exc:
            raise AgentLaunchError(str(exc)) from exc
    else:
        settings = _resolve_openhands(
            profile, stores, mcp_config, catalog, dangling_mcp
        )
    if not isinstance(settings, OpenHandsAgentSettings | ACPAgentSettings):
        raise AgentLaunchError(f"Unsupported agent kind {settings.agent_kind!r}")
    return ResolvedLaunch(
        settings=settings,
        profile=LaunchedAgentProfile(
            agent_profile_id=profile.id,
            revision=profile.revision,
            secret_refs=profile.secret_refs,
        ),
    )


def _resolve_openhands(
    profile: OpenHandsAgentProfile,
    stores: LaunchStores,
    mcp_config: dict[str, MCPServer],
    catalog: list[Skill],
    dangling_mcp: list[str],
) -> OpenHandsAgentSettings | ACPAgentSettings:
    llm = _load_llm(stores, profile.llm_profile_ref)
    if llm is None or dangling_mcp:
        raise UnresolvedProfileReferences(
            llm_profile_ref=profile.llm_profile_ref if llm is None else None,
            mcp_server_refs=dangling_mcp,
        )
    try:
        return _build_openhands_settings(
            profile,
            # A profile cannot express streaming; LLM degrades to non-streaming
            # when the runtime wires no token callback.
            llm.model_copy(update={"stream": True}),
            mcp_config,
            _apply_disabled_skills(catalog, profile.disabled_skills),
        )
    except ValueError as exc:
        raise AgentLaunchError(str(exc)) from exc


def _load_stored_profile(
    profile_id: UUID, stores: LaunchStores
) -> OpenHandsAgentProfile | ACPAgentProfile:
    if stores.agent_profiles is None:
        raise ProfileNotFound(f"Agent profile with id '{profile_id}' not found")
    try:
        name = stores.agent_profiles.name_for_id(profile_id)
        if name is None:
            raise ProfileNotFound(f"Agent profile with id '{profile_id}' not found")
        return stores.agent_profiles.load(name)
    except ProfileNotFound:
        raise
    except FileNotFoundError as exc:
        raise ProfileNotFound(
            f"Agent profile with id '{profile_id}' not found"
        ) from exc
    except TimeoutError as exc:
        raise LaunchStoreError("Agent profile store is busy", retryable=True) from exc
    except (OSError, ValueError) as exc:
        raise LaunchStoreError(f"Could not load agent profile: {exc}") from exc


def _load_skills(stores: LaunchStores) -> list[Skill]:
    try:
        return list(stores.skills())
    except Exception as exc:
        raise LaunchStoreError(f"Skill discovery failed: {exc}") from exc


def _load_llm(stores: LaunchStores, name: str) -> LLM | None:
    from openhands.sdk.llm.provider_connection_store import ProviderConnectionNotFound

    try:
        return stores.llm_profiles.load(name, cipher=stores.cipher)
    except (FileNotFoundError, ProviderConnectionNotFound):
        return None
    except TimeoutError as exc:
        raise LaunchStoreError("LLM profile store is busy", retryable=True) from exc
    except (OSError, ValueError) as exc:
        raise LaunchStoreError(f"Could not load LLM profile {name!r}: {exc}") from exc
