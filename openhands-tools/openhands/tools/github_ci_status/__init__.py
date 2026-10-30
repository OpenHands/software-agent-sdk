"""GitHub CI status tool package.

Provides a read-only ``github_ci_status`` tool that reports the CI status of a
pull request head or a commit using only GitHub App-token-compatible endpoints
(``/actions/runs?head_sha=`` and ``/actions/runs/{id}/jobs``), with best-effort
enrichment from the Checks API and commit statuses.

Usage:
    from openhands.tools.github_ci_status import GithubCiStatusTool

    agent = Agent(
        llm=llm,
        tools=[Tool(name=GithubCiStatusTool.name)],
    )
"""

from openhands.tools.github_ci_status.definition import (
    CiResult,
    GithubCiStatusAction,
    GithubCiStatusObservation,
    GithubCiStatusTool,
)
from openhands.tools.github_ci_status.impl import GithubCiStatusExecutor


__all__ = [
    "CiResult",
    "GithubCiStatusAction",
    "GithubCiStatusExecutor",
    "GithubCiStatusObservation",
    "GithubCiStatusTool",
]
