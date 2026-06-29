"""Sub-agents router for OpenHands Agent Server.

A single read endpoint listing the file-based and built-in sub-agents available
to a workspace (mirrors ``skills_router``'s ``POST /skills``). No CRUD: the
catalog is discovered, not mutated. Named ``sub_agents`` to distinguish these
delegate agents from the top-level agent and ``agent_profiles``.
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from openhands.sdk.context.condenser import CondenserBase
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.subagent import (
    AgentDefinition,
    AgentDefinitionLevel,
    discover_agents,
)
from openhands.tools.preset.default import discover_builtin_agents


sub_agents_router = APIRouter(prefix="/sub-agents", tags=["Sub Agents"])


class SubAgentsRequest(BaseModel):
    """Request body for listing sub-agents."""

    load_user: bool = Field(
        default=True,
        description="Load user agents from ~/.agents/agents and ~/.openhands/agents",
    )
    load_project: bool = Field(
        default=True,
        description="Load project agents from the workspace",
    )
    load_builtin: bool = Field(
        default=True,
        description="Load SDK built-in agents (general-purpose, code-explorer, ...)",
    )
    project_dir: str | None = Field(
        default=None,
        description="Workspace directory path for project agents",
    )


class SubAgentInfo(BaseModel):
    """Lossless view of an ``AgentDefinition`` returned by the API.

    Every frontmatter field plus the discovered ``level``/``source``, an
    ``is_builtin`` flag, and the inline ``system_prompt`` (Markdown body) so a
    detail view needs no extra fetch.
    """

    name: str
    description: str = ""
    model: str = "inherit"
    color: str | None = None
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    when_to_use_examples: list[str] = Field(default_factory=list)
    permission_mode: str | None = None
    max_iteration_per_run: int | None = None
    max_budget_per_run: float | None = None
    mcp_servers: dict[str, Any] | None = None
    profile_store_dir: str | None = None
    hooks: HookConfig | None = None
    condenser: CondenserBase | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    level: AgentDefinitionLevel | None = None
    source: str | None = None
    is_builtin: bool = False

    @classmethod
    def from_definition(cls, agent_def: AgentDefinition) -> "SubAgentInfo":
        return cls(
            name=agent_def.name,
            description=agent_def.description,
            model=agent_def.model,
            color=agent_def.color,
            tools=agent_def.tools,
            skills=agent_def.skills,
            system_prompt=agent_def.system_prompt,
            when_to_use_examples=agent_def.when_to_use_examples,
            permission_mode=agent_def.permission_mode,
            max_iteration_per_run=agent_def.max_iteration_per_run,
            max_budget_per_run=agent_def.max_budget_per_run,
            mcp_servers=agent_def.mcp_servers,
            profile_store_dir=agent_def.profile_store_dir,
            hooks=agent_def.hooks,
            condenser=agent_def.condenser,
            metadata=agent_def.metadata,
            level=agent_def.level,
            source=agent_def.source,
            is_builtin=agent_def.level == "builtin",
        )


class SubAgentsResponse(BaseModel):
    """Response containing all available sub-agents."""

    agents: list[SubAgentInfo]


@sub_agents_router.post("", response_model=SubAgentsResponse)
def get_sub_agents(request: SubAgentsRequest) -> SubAgentsResponse:
    """List file-based and built-in sub-agents for the workspace.

    Merged first-wins by name with precedence project > user > builtin. Read-only:
    it registers nothing into the conversation registry.
    """
    discovered = discover_agents(
        project_dir=request.project_dir,
        include_project=request.load_project,
        include_user=request.load_user,
    )
    builtins = discover_builtin_agents() if request.load_builtin else []

    # project > user (from discover_agents) > builtin: first wins on name clash.
    seen_names: set[str] = set()
    agents: list[SubAgentInfo] = []
    for agent_def in (*discovered, *builtins):
        if agent_def.name in seen_names:
            continue
        seen_names.add(agent_def.name)
        agents.append(SubAgentInfo.from_definition(agent_def))

    return SubAgentsResponse(agents=agents)
