"""Models for GitHub autospawn webhook integration."""

from pydantic import BaseModel, Field, SecretStr

from openhands.sdk import LLM, AgentBase


class GitHubAgentConfig(BaseModel):
    """Configuration for an agent spawned by a GitHub webhook trigger."""

    task: str = Field(
        ...,
        description="Task prompt to give to the agent. Can include template variables.",
    )
    agent: AgentBase = Field(
        ...,
        description="Agent configuration including LLM and tools",
    )
    max_iterations: int = Field(
        default=500,
        ge=1,
        description="Maximum number of iterations the agent will run",
    )


class GitHubTriggerConfig(BaseModel):
    """Configuration for a single GitHub webhook trigger."""

    event: str = Field(
        ...,
        description='GitHub event type (e.g., "pull_request", "issues", "push")',
    )
    action: str | None = Field(
        default=None,
        description='Optional action filter (e.g., "opened", "labeled", etc.)',
    )
    repo: str = Field(
        ...,
        description='Repository filter in "owner/repo" format or wildcard pattern',
    )
    branch: str | None = Field(
        default=None,
        description="Optional branch filter (for push events)",
    )
    agent_config: GitHubAgentConfig = Field(
        ...,
        description="Agent configuration to use when trigger matches",
    )


class GitHubWebhookConfig(BaseModel):
    """Top-level configuration for GitHub autospawn webhooks."""

    github_secret: SecretStr | None = Field(
        default=None,
        description="GitHub webhook secret for HMAC signature verification",
    )
    triggers: list[GitHubTriggerConfig] = Field(
        default_factory=list,
        description="List of trigger configurations",
    )
    workspace_base_dir: str | None = Field(
        default=None,
        description="Base directory for workspace creation. If None, uses system temp",
    )
    cleanup_on_success: bool = Field(
        default=True,
        description="Whether to cleanup workspace after successful execution",
    )
    cleanup_on_failure: bool = Field(
        default=False,
        description="Whether to cleanup workspace after failed execution",
    )
