"""Extension configuration and resolution.

This module defines the declarative specification for what extensions a
conversation should load (``ExtensionConfig``) and the materialized result
after loading and merging (``ResolvedExtensions``).

Import note:
    This module imports from ``skills``, ``plugin``, and ``hooks``.  Because
    those packages import from ``extensions.fetch`` at package-init time, this
    module must **not** be re-exported from ``extensions/__init__.py`` until the
    loader functions are migrated into ``extensions/`` (removing the reverse
    dependency).  Consumers import directly::

        from openhands.sdk.extensions.config import ExtensionConfig
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.utils import merge_mcp_configs
from openhands.sdk.plugin import (
    Plugin,
    PluginSource,
    ResolvedPluginSource,
    fetch_plugin_with_resolution,
)
from openhands.sdk.skills import Skill, load_available_skills
from openhands.sdk.skills.skill import DEFAULT_MARKETPLACE_PATH
from openhands.sdk.subagent.schema import AgentDefinition


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Resolved result
# ---------------------------------------------------------------------------


class ResolvedExtensions(BaseModel):
    """Materialized extensions after loading and merging.

    This is the output of ``ExtensionConfig.resolve()``.  The conversation
    destructures it and applies each part to the appropriate target:

    - ``skills`` → ``agent.agent_context.skills`` (prompt injection)
    - ``mcp_config`` → ``agent.mcp_config`` (MCP tool creation)
    - ``hooks`` → ``HookEventProcessor`` (event-level callbacks)
    - ``agents`` → subagent registry
    - ``resolved_plugins`` → persisted for deterministic resume
    """

    skills: list[Skill] = Field(default_factory=list)
    mcp_config: dict[str, Any] = Field(default_factory=dict)
    hooks: HookConfig | None = None
    agents: list[AgentDefinition] = Field(default_factory=list)
    resolved_plugins: list[ResolvedPluginSource] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Declarative config
# ---------------------------------------------------------------------------


class ExtensionConfig(BaseModel):
    """Declarative specification of what extensions to load.

    Holds both explicit extension objects (skills, hooks) and flags that
    trigger loading from well-known locations (user dir, public repo,
    project workspace).  Nothing is fetched or loaded at construction
    time — all I/O is deferred to ``resolve()``.

    Merge precedence (later overrides earlier for name collisions):

        auto-loaded skills (public < user < project)
          → explicit skills
            → plugin skills (in plugin-list order)

    Hooks use concatenation semantics (all hooks run); explicit hooks
    execute before plugin hooks.  MCP configs merge by server name
    (last wins).
    """

    # -- Explicit extensions (already materialized) -----------------------

    skills: list[Skill] = Field(
        default_factory=list,
        description="Pre-built Skill objects. Override auto-loaded skills by name.",
    )
    plugins: list[PluginSource] = Field(
        default_factory=list,
        description="Plugin sources to fetch and load.",
    )
    hook_config: HookConfig | None = Field(
        default=None,
        description="Explicit hook configuration. Runs before plugin hooks.",
    )

    # -- Auto-loading flags -----------------------------------------------

    load_user_extensions: bool = Field(
        default=False,
        description="Load extensions from ~/.openhands/skills/.",
    )
    load_public_extensions: bool = Field(
        default=False,
        description="Load extensions from the public OpenHands extensions repo.",
    )
    marketplace_path: str | None = Field(
        default=DEFAULT_MARKETPLACE_PATH,
        description=(
            "Marketplace JSON path within the public skills repo. "
            "None = load all public skills without filtering."
        ),
    )

    # -- Resolution -------------------------------------------------------

    def resolve(
        self,
        work_dir: str | Path | None = None,
        *,
        existing_skills: list[Skill] | None = None,
        existing_mcp_config: dict[str, Any] | None = None,
    ) -> ResolvedExtensions:
        """Load, merge, and return resolved extensions.

        This is the single authoritative merge path.  It performs all
        network I/O (git clone/pull for public skills and remote plugins)
        and returns a fully materialized ``ResolvedExtensions``.

        Args:
            work_dir: Project workspace directory.  Used for loading
                project-level skills.  ``None`` skips project skills.
            existing_skills: Skills already on the agent (e.g. from
                ``AgentContext.skills`` during the deprecation period).
                Treated as the lowest-precedence layer.
            existing_mcp_config: MCP config already on the agent.
                Used as the base for MCP merging.

        Returns:
            A ``ResolvedExtensions`` with everything merged.

        Precedence for skills (later overrides earlier by name):
            1. existing_skills          (backward-compat, lowest)
            2. auto-loaded public
            3. auto-loaded user
            4. auto-loaded project
            5. self.skills              (explicit on config)
            6. plugin skills            (in plugin-list order)
        """
        # -- 1. Start with existing agent skills (backward-compat layer) --
        skills_by_name: dict[str, Skill] = {}
        if existing_skills:
            for s in existing_skills:
                skills_by_name[s.name] = s

        # -- 2-4. Auto-load from well-known locations ---------------------
        if self.load_public_extensions or self.load_user_extensions or work_dir:
            auto = load_available_skills(
                work_dir=work_dir,
                include_user=self.load_user_extensions,
                include_project=work_dir is not None,
                include_public=self.load_public_extensions,
                marketplace_path=self.marketplace_path,
            )
            for name, skill in auto.items():
                skills_by_name[name] = skill

        # -- 5. Explicit skills on config (override auto-loaded) ----------
        for skill in self.skills:
            if skill.name in skills_by_name:
                logger.debug(
                    "Explicit skill '%s' overrides auto-loaded skill",
                    skill.name,
                )
            skills_by_name[skill.name] = skill

        # -- 6. Plugins (fetch → load → merge) ---------------------------
        mcp_config: dict[str, Any] = (
            dict(existing_mcp_config) if existing_mcp_config else {}
        )
        all_hooks: list[HookConfig] = []
        all_agents: list[AgentDefinition] = []
        resolved_plugins: list[ResolvedPluginSource] = []

        for spec in self.plugins:
            path, resolved_ref = fetch_plugin_with_resolution(
                source=spec.source,
                ref=spec.ref,
                repo_path=spec.repo_path,
            )
            resolved_plugins.append(
                ResolvedPluginSource.from_plugin_source(spec, resolved_ref)
            )

            plugin = Plugin.load(path)
            logger.debug(
                "Loaded plugin '%s' from %s%s",
                plugin.manifest.name,
                spec.source,
                f" @ {resolved_ref[:8]}" if resolved_ref else "",
            )

            # Skills: name-based last-wins
            for skill in plugin.get_all_skills():
                if skill.name in skills_by_name:
                    logger.debug(
                        "Plugin skill '%s' overrides existing skill",
                        skill.name,
                    )
                skills_by_name[skill.name] = skill

            # MCP: server-name-based last-wins
            mcp_config = merge_mcp_configs(mcp_config, plugin.mcp_config)

            # Hooks: concatenate
            if plugin.hooks and not plugin.hooks.is_empty():
                all_hooks.append(plugin.hooks)

            # Agents: collect
            if plugin.agents:
                all_agents.extend(plugin.agents)

        # -- Combine hooks (explicit first, then plugins) -----------------
        final_hooks: HookConfig | None = None
        hook_parts: list[HookConfig] = []
        if self.hook_config is not None:
            hook_parts.append(self.hook_config)
        hook_parts.extend(all_hooks)
        if hook_parts:
            final_hooks = HookConfig.merge(hook_parts)

        return ResolvedExtensions(
            skills=list(skills_by_name.values()),
            mcp_config=mcp_config,
            hooks=final_hooks,
            agents=all_agents,
            resolved_plugins=resolved_plugins,
        )
