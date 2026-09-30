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
from openhands.sdk.llm.meta_profile_store import MetaProfile
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


class MetaProfileLoader(Protocol):
    def load(self, name: str) -> MetaProfile: ...


@dataclass(frozen=True, kw_only=True)
class LaunchStores:
    """The stores a launch reads, as seen by the process that holds them."""

    llm_profiles: LLMProfileLoader
    mcp_config: Mapping[str, MCPServer]
    skills: Callable[[], Sequence[Skill]]
    agent_profiles: AgentProfileLoader | None = None
    meta_profiles: MetaProfileLoader | None = None
    llm_profile_names: Callable[[], Sequence[str]] | None = None
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


def resolve(
    source: AgentProfileSource,
    stores: LaunchStores,
    *,
    llm_profile_ref: str | None = None,
) -> ResolvedLaunch:
    """Resolve a stored profile id or an inline profile into a ``ResolvedLaunch``.

    Every reference is copied into the result, so the runtime that finalizes it
    needs no store. All dangling references are raised together as
    :class:`UnresolvedProfileReferences`; a store that cannot be read raises
    :class:`LaunchStoreError`; an unknown profile id raises ``ProfileNotFound``.
    """
    if isinstance(source, UUID):
        profile = _load_stored_profile(source, stores)
        inline = False
    else:
        profile = source
        inline = True
    if llm_profile_ref is not None and not isinstance(profile, OpenHandsAgentProfile):
        raise AgentLaunchError(
            "agent_launch_additions.llm_profile_ref applies only to an "
            "OpenHands Agent Profile"
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
            profile, stores, mcp_config, catalog, dangling_mcp, llm_profile_ref
        )
    if not isinstance(settings, OpenHandsAgentSettings | ACPAgentSettings):
        raise AgentLaunchError(f"Unsupported agent kind {settings.agent_kind!r}")
    return ResolvedLaunch(
        settings=settings,
        profile=LaunchedAgentProfile(
            agent_profile_id=profile.id,
            revision=profile.revision,
            secret_refs=profile.secret_refs,
            inline=inline,
            llm_profile_ref=llm_profile_ref,
        ),
    )


def _resolve_openhands(
    profile: OpenHandsAgentProfile,
    stores: LaunchStores,
    mcp_config: dict[str, MCPServer],
    catalog: list[Skill],
    dangling_mcp: list[str],
    llm_profile_ref: str | None,
) -> OpenHandsAgentSettings | ACPAgentSettings:
    llm_ref = llm_profile_ref or profile.llm_profile_ref
    llm = _load_llm(stores, llm_ref)
    meta_profile, meta_llms, dangling_meta, dangling_meta_llms = _load_meta_profile(
        profile, stores
    )
    if llm is None or dangling_mcp or dangling_meta or dangling_meta_llms:
        raise UnresolvedProfileReferences(
            llm_profile_ref=llm_ref if llm is None else None,
            mcp_server_refs=dangling_mcp,
            meta_profile_ref=dangling_meta,
            meta_profile_llm_refs=dangling_meta_llms,
        )
    try:
        return _build_openhands_settings(
            profile,
            # A profile cannot express streaming; LLM degrades to non-streaming
            # when the runtime wires no token callback.
            llm.model_copy(update={"stream": True}),
            mcp_config,
            _apply_disabled_skills(catalog, profile.disabled_skills),
            meta_profile=meta_profile,
            meta_profile_llms=meta_llms,
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


def _load_meta_profile(
    profile: OpenHandsAgentProfile, stores: LaunchStores
) -> tuple[MetaProfile | None, dict[str, LLM], str | None, list[str]]:
    name = profile.meta_profile_ref
    if not profile.enable_classify_and_switch_llm_tool or name is None:
        return None, {}, None, []
    if stores.meta_profiles is None:
        return None, {}, name, []
    try:
        meta = stores.meta_profiles.load(name)
    except FileNotFoundError:
        return None, {}, name, []
    except TimeoutError as exc:
        raise LaunchStoreError("Meta-profile store is busy", retryable=True) from exc
    except (OSError, ValueError) as exc:
        raise LaunchStoreError(f"Could not load meta-profile {name!r}: {exc}") from exc

    llms: dict[str, LLM] = {}
    dangling: list[str] = []
    refs = [meta.classifier_model, *(cls.model for cls in meta.classes)]
    for ref in dict.fromkeys(refs):
        llm = _load_llm(stores, ref)
        if llm is None:
            dangling.append(ref)
        else:
            llms[ref] = llm
    if meta.prompt_template is not None and stores.llm_profile_names is not None:
        # Direct routing may pick any saved LLM profile, so an unreadable one
        # that the meta-profile does not name is skipped rather than fatal.
        for ref in stores.llm_profile_names():
            if ref in llms:
                continue
            try:
                llm = _load_llm(stores, ref)
            except LaunchStoreError:
                continue
            if llm is not None:
                llms[ref] = llm
    return meta, llms, None, dangling
