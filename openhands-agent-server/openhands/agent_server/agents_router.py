"""Agents router for OpenHands Agent Server.

Exposes a single read endpoint that lists the file-based and built-in
sub-agents available to a workspace, mirroring the read path of
``skills_router`` (``POST /skills``). There is intentionally no CRUD here:
the agents catalog is discovered from disk + built-ins and is not mutated
through this API.
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from openhands.sdk.subagent import (
    AgentDefinition,
    AgentDefinitionLevel,
    discover_agents,
)
from openhands.tools.preset.default import discover_builtin_agents


agents_router = APIRouter(prefix="/agents", tags=["Agents"])


class AgentsRequest(BaseModel):
    """Request body for listing agents."""

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


class AgentInfo(BaseModel):
    """Agent information returned by the API.

    Carries ``system_prompt`` (the Markdown body) inline so a detail view needs
    no separate content fetch.
    """

    name: str
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    model: str = "inherit"
    color: str | None = None
    when_to_use_examples: list[str] = Field(default_factory=list)
    level: AgentDefinitionLevel | None = None
    source: str | None = None
    is_builtin: bool = False
    system_prompt: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_definition(cls, agent_def: AgentDefinition) -> "AgentInfo":
        return cls(
            name=agent_def.name,
            description=agent_def.description,
            tools=agent_def.tools,
            model=agent_def.model,
            color=agent_def.color,
            when_to_use_examples=agent_def.when_to_use_examples,
            level=agent_def.level,
            source=agent_def.source,
            is_builtin=agent_def.level == "builtin",
            system_prompt=agent_def.system_prompt,
            metadata=agent_def.metadata,
        )


class AgentsResponse(BaseModel):
    """Response containing all available agents."""

    agents: list[AgentInfo]


@agents_router.post("", response_model=AgentsResponse)
def get_agents(request: AgentsRequest) -> AgentsResponse:
    """List file-based and built-in agents available to the workspace.

    Agents are merged with the same precedence used when registering them for a
    conversation (first wins for duplicate names):

    1. Project agents (highest) - ``{workspace}/.agents/agents`` then ``.openhands``
    2. User agents - ``~/.agents/agents`` then ``~/.openhands/agents``
    3. Built-in agents (lowest) - shipped with the SDK tools preset

    This is a read-only listing; it does not register agents into the
    conversation registry (that stays conversation-scoped).

    Args:
        request: AgentsRequest selecting which sources to load.

    Returns:
        AgentsResponse with the merged, de-duplicated agent catalog.
    """
    discovered = discover_agents(
        project_dir=request.project_dir,
        include_project=request.load_project,
        include_user=request.load_user,
    )
    builtins = discover_builtin_agents() if request.load_builtin else []

    # discover_agents already orders project > user; built-ins come last so a
    # project/user agent with the same name shadows a built-in.
    seen_names: set[str] = set()
    agents: list[AgentInfo] = []
    for agent_def in (*discovered, *builtins):
        if agent_def.name in seen_names:
            continue
        seen_names.add(agent_def.name)
        agents.append(AgentInfo.from_definition(agent_def))

    return AgentsResponse(agents=agents)
