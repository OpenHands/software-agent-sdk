"""Action, observation, and tool definitions for the github_ci_status tool."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from rich.text import Text

from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


OverallStatusType = Literal["success", "failure", "pending", "neutral"]
CiResultKind = Literal["workflow_run", "check_run", "commit_status"]


class CiResult(BaseModel):
    """One CI signal: a workflow run, a check run, or a commit status."""

    name: str = Field(description="Workflow, check, or status-context name.")
    kind: CiResultKind = Field(description="Which CI surface produced this result.")
    status: str = Field(
        default="",
        description=(
            "Raw status or state: e.g. 'completed', 'in_progress', 'queued', "
            "'success', 'pending', 'error'."
        ),
    )
    conclusion: str | None = Field(
        default=None,
        description=(
            "Raw conclusion when the work is finished, e.g. 'success', 'failure', "
            "'timed_out', 'skipped'. None for commit statuses and unfinished runs."
        ),
    )
    url: str | None = Field(
        default=None, description="Browser URL for the run, check, or status."
    )
    details: list[str] = Field(
        default_factory=list,
        description="Failing job/step lines for non-passing workflow runs.",
    )


class GithubCiStatusAction(Action):
    """Read the CI status of a pull request head or a commit."""

    repository: str = Field(
        description=(
            'Repository in "owner/repo" form, e.g. "OpenHands/OpenHands". '
            "A github.com clone URL is also accepted."
        )
    )
    pull_number: int | None = Field(
        default=None,
        description=(
            "Pull request number whose current head commit to check. "
            "Provide exactly one of pull_number or sha."
        ),
    )
    sha: str | None = Field(
        default=None,
        description=(
            "Commit SHA to check, e.g. for a push event. "
            "Provide exactly one of pull_number or sha."
        ),
    )
    include_jobs: bool = Field(
        default=True,
        description=(
            "Fetch failing job and step names for non-passing workflow runs "
            "(a few extra API calls). Never used when every run passes."
        ),
    )

    @field_validator("repository", mode="before")
    @classmethod
    def _normalize_repository(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        repo = value.strip().removesuffix(".git")
        for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
            if repo.startswith(prefix):
                repo = repo[len(prefix) :]
        return repo.strip("/")

    @field_validator("sha", mode="before")
    @classmethod
    def _normalize_sha(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _require_exactly_one_target(self) -> Self:
        if (self.pull_number is None) == (self.sha is None):
            raise ValueError("Provide exactly one of pull_number or sha.")
        return self

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("GitHub CI status: ", style="bold cyan")
        content.append(self.repository)
        target = f"PR #{self.pull_number}" if self.pull_number else f"commit {self.sha}"
        content.append(f" ({target})")
        return content


class GithubCiStatusObservation(Observation):
    """CI status report for a pull request head or a commit."""

    repository: str = Field(default="", description='Repository as "owner/repo".')
    sha: str = Field(default="", description="Commit SHA the status was computed for.")
    pull_number: int | None = Field(
        default=None,
        description="Pull request number when the target was a pull request.",
    )
    overall: OverallStatusType = Field(
        default="neutral",
        description=(
            "Rollup verdict over every visible signal. Any failure wins over any "
            "pending run, which wins over success. 'neutral' means no CI signal "
            "was found at all."
        ),
    )
    workflow_runs: list[CiResult] = Field(
        default_factory=list,
        description="GitHub Actions workflow runs for the head SHA.",
    )
    other_checks: list[CiResult] = Field(
        default_factory=list,
        description=(
            "Non-Actions signals (Checks API check runs and commit statuses) with "
            "Actions-app entries removed. Best-effort: some tokens cannot read them."
        ),
    )
    unavailable_sources: list[str] = Field(
        default_factory=list,
        description=(
            "CI sources this token could not read, with the permission hint "
            "needed to fix them."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        icons = {
            "success": ("✓ ", "green"),
            "failure": ("✗ ", "red"),
            "pending": ("… ", "yellow"),
            "neutral": ("• ", "dim"),
        }
        icon, style = icons[self.overall]
        content.append(icon, style=style)
        content.append("CI status", style="bold")
        content.append(f" {self.repository}", style="dim")
        if self.sha:
            content.append(f" @ {self.sha[:12]}", style="dim")
        content.append("\n")
        content.append(f"Overall: {self.overall.upper()}", style=style)
        return content


_DESCRIPTION = """\
Report the CI status of a pull request head or a commit as a single rollup \
verdict plus per-check results.

This tool uses only GitHub App-token-compatible endpoints: the Actions API \
(actions/runs?head_sha=... and actions/runs/{run_id}/jobs). Unlike `gh pr \
checks`, the check-runs API, and the GraphQL statusCheckRollup - which all \
return 403 for GitHub App installation tokens that lack the Checks permission - \
the Actions API works with just the Actions and Pull requests permissions, so \
this tool is the reliable way to gate on CI when authenticated as a GitHub App.

Non-Actions checks (Checks API check runs and commit statuses) are added on a \
best-effort basis; when the token cannot read them the report says so instead \
of failing.

The rollup counts every visible signal: any failing check makes the overall \
status 'failure' even if other runs are still in progress; otherwise any \
pending run makes it 'pending'; all-passing is 'success'.

Requires a GitHub token: conversation secrets (github_token or GITHUB_TOKEN) \
or the GITHUB_TOKEN / GH_TOKEN environment variable. For GitHub Enterprise, set \
GITHUB_API_URL to the instance API base."""


class GithubCiStatusTool(
    ToolDefinition[GithubCiStatusAction, GithubCiStatusObservation]
):
    """Read PR/commit CI status via App-token-compatible GitHub API endpoints."""

    user_selectable: ClassVar[bool] = True
    catalog_description: ClassVar[str] = (
        "Read pull request or commit CI status from the GitHub Actions API, "
        "including under GitHub App tokens."
    )

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("GithubCiStatusTool does not accept parameters")

        # Import here to keep module import light and avoid any import cycles.
        from openhands.tools.github_ci_status.impl import GithubCiStatusExecutor

        return [
            cls(
                description=_DESCRIPTION,
                action_type=GithubCiStatusAction,
                observation_type=GithubCiStatusObservation,
                executor=GithubCiStatusExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]


# Automatically register when this module is imported.
register_tool(GithubCiStatusTool.name, GithubCiStatusTool)
