"""Executor for the github_ci_status tool.

Reads CI status through the GitHub Actions API - ``/actions/runs?head_sha=...``
and ``/actions/runs/{run_id}/jobs`` - because those endpoints are readable with
GitHub App installation tokens, while ``gh pr checks``, ``/check-runs``, and the
GraphQL ``statusCheckRollup`` answer 403 when the App lacks the Checks
permission. The Checks API and commit statuses are still merged in on a
best-effort basis so third-party checks appear whenever the token can see them.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import httpx

from openhands.sdk.tool import ToolExecutor
from openhands.tools.github_ci_status.definition import (
    CiResult,
    GithubCiStatusAction,
    GithubCiStatusObservation,
    OverallStatusType,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


# Same candidate names as the agent-server's git provider token resolution.
_TOKEN_SECRET_CANDIDATES: tuple[str, ...] = (
    "github_token",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "github",
)
_API_VERSION = "2022-11-28"
_ACCEPT = "application/vnd.github+json"
_REQUEST_TIMEOUT = 15.0
_MAX_PAGES = 20
_PAGE_SIZE = 100

_FAIL_CONCLUSIONS = frozenset(
    {
        "failure",
        "timed_out",
        "action_required",
        "cancelled",
        "stale",
        "startup_failure",
        "error",
    }
)
_SUCCESS_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
_FAIL_STATES = frozenset({"failure", "error"})


class GithubAPIError(RuntimeError):
    """A GitHub REST call answered with a non-success status."""

    def __init__(self, path: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.status_code = status_code


def _api_base_url() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _resolve_token(conversation: LocalConversation | None) -> str | None:
    """Find a GitHub token in conversation secrets, then the environment."""
    if conversation is not None:
        registry = conversation.state.secret_registry
        for name in _TOKEN_SECRET_CANDIDATES:
            value = registry.get_secret_value(name)
            if value:
                return value
    for name in _TOKEN_SECRET_CANDIDATES:
        value = os.environ.get(name)
        if value:
            if conversation is not None:
                # Track env-sourced tokens so tool output masking covers them.
                conversation.state.secret_registry.track_exported_values({name: value})
            return value
    return None


def _permission_hint(status_code: int, path: str) -> str:
    if status_code in (401, 403):
        if "/actions/" in path:
            return (
                "The token was refused; GitHub App installation tokens need the "
                "'actions: read' permission for this endpoint."
            )
        if "/pulls/" in path:
            return (
                "The token was refused; GitHub App installation tokens need the "
                "'pull requests: read' permission for this endpoint."
            )
        return "The token was refused; check its permissions for this endpoint."
    if status_code == 404:
        return "Not found: check the repository (and pull request) names."
    return ""


def _fetch_page(
    client: httpx.Client, token: str, path: str, params: dict
) -> dict[str, Any] | list[Any]:
    response = client.get(
        f"{_api_base_url()}{path}",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "openhands-github-ci-status",
        },
    )
    if response.status_code >= 400:
        message = f"GitHub API {path} failed with HTTP {response.status_code}."
        hint = _permission_hint(response.status_code, path)
        if hint:
            message = f"{message} {hint}"
        raise GithubAPIError(path, response.status_code, message)
    payload = response.json()
    if not isinstance(payload, (dict, list)):
        raise GithubAPIError(
            path, response.status_code, f"GitHub API {path} returned unexpected JSON."
        )
    return payload


def _fetch_items(
    client: httpx.Client, token: str, path: str, key: str | None = None
) -> list[dict]:
    """Fetch every page of a GitHub list endpoint.

    ``key`` names the list field for object-shaped responses (``workflow_runs``,
    ``jobs``, ``check_runs``); ``None`` means the response body is the list
    (commit statuses).
    """
    items: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        params = {"per_page": _PAGE_SIZE, "page": page}
        data = _fetch_page(client, token, path, params)
        if key:
            batch = data.get(key) if isinstance(data, dict) else None
        else:
            batch = data if isinstance(data, list) else None
        if not isinstance(batch, list) or not batch:
            break
        items.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < _PAGE_SIZE:
            break
    return items


def _classify(
    status: str, conclusion: str | None
) -> Literal["success", "failure", "pending"]:
    conclusion = conclusion or ""
    if conclusion in _FAIL_CONCLUSIONS or status in _FAIL_STATES:
        return "failure"
    if conclusion in _SUCCESS_CONCLUSIONS or status == "success":
        return "success"
    return "pending"


def _overall(results: list[CiResult]) -> OverallStatusType:
    if not results:
        return "neutral"
    classes = {_classify(r.status, r.conclusion) for r in results}
    if "failure" in classes:
        return "failure"
    if "pending" in classes:
        return "pending"
    return "success"


def _job_failure_lines(jobs: list[dict]) -> list[str]:
    lines = []
    for job in jobs:
        status = job.get("status") or ""
        conclusion = job.get("conclusion")
        if status == "completed" and conclusion in _SUCCESS_CONCLUSIONS:
            continue
        failed_steps = [
            step.get("name") or "unnamed step"
            for step in job.get("steps") or []
            if isinstance(step, dict) and step.get("conclusion") in _FAIL_CONCLUSIONS
        ]
        line = f"{job.get('name') or 'unnamed job'} [{conclusion or status or '?'}]"
        if failed_steps:
            line += f" - failed steps: {', '.join(failed_steps)}"
        if job.get("html_url"):
            line += f" - {job['html_url']}"
        lines.append(line)
    return lines


def _other_checks(
    client: httpx.Client, token: str, repository: str, sha: str
) -> tuple[list[CiResult], list[str]]:
    """Merge check runs and commit statuses, degrading on permission errors."""
    results: list[CiResult] = []
    notes: list[str] = []

    try:
        check_runs = _fetch_items(
            client, token, f"/repos/{repository}/commits/{sha}/check-runs", "check_runs"
        )
    except GithubAPIError as exc:
        notes.append(
            f"check-runs: unavailable (HTTP {exc.status_code}). GitHub App "
            "installation tokens need the 'checks: read' permission for this "
            "endpoint; the workflow-run results above come from the Actions API "
            "and are complete without it."
        )
    else:
        for run in check_runs:
            app = run.get("app")
            app_slug = app.get("slug") if isinstance(app, dict) else None
            if app_slug == "github-actions":
                continue  # Already covered by the workflow-run results.
            results.append(
                CiResult(
                    name=run.get("name") or "unnamed check",
                    kind="check_run",
                    status=run.get("status") or "completed",
                    conclusion=run.get("conclusion"),
                    url=run.get("html_url"),
                )
            )

    try:
        statuses = _fetch_items(
            client, token, f"/repos/{repository}/commits/{sha}/statuses"
        )
    except GithubAPIError as exc:
        notes.append(
            f"commit statuses: unavailable (HTTP {exc.status_code}). GitHub App "
            "installation tokens need the 'statuses: read' permission for this "
            "endpoint."
        )
    else:
        seen: set[str] = set()
        for status in statuses:  # Newest first: keep only the latest per context.
            name = status.get("context") or "unnamed status"
            if name in seen:
                continue
            seen.add(name)
            results.append(
                CiResult(
                    name=name,
                    kind="commit_status",
                    status=status.get("state") or "pending",
                    conclusion=None,
                    url=status.get("target_url"),
                )
            )

    return results, notes


_MARKS = {"success": "PASS", "failure": "FAIL", "pending": "PENDING"}


def _result_lines(results: list[CiResult]) -> list[str]:
    lines = []
    for result in results:
        mark = _MARKS[_classify(result.status, result.conclusion)]
        tail = result.conclusion or result.status or "?"
        line = f"  [{mark}] {result.name} - {tail}"
        if result.url:
            line += f" - {result.url}"
        lines.append(line)
        lines.extend(f"      {detail}" for detail in result.details)
    return lines


def _format_report(
    repository: str,
    sha: str,
    pull_number: int | None,
    overall: str,
    workflow_runs: list[CiResult],
    other_checks: list[CiResult],
    notes: list[str],
) -> str:
    target = f" (PR #{pull_number})" if pull_number is not None else ""
    lines = [f"GitHub CI status for {repository}{target}, commit {sha}", ""]

    all_results = workflow_runs + other_checks
    counts = {mark: 0 for mark in _MARKS.values()}
    for result in all_results:
        counts[_MARKS[_classify(result.status, result.conclusion)]] += 1
    counts_text = ", ".join(f"{n} {mark.lower()}" for mark, n in counts.items() if n)
    lines.append(
        f"Overall: {overall.upper()}" + (f" - {counts_text}" if counts_text else "")
    )
    lines.append("")

    lines.append("Workflow runs (Actions API; visible to GitHub App tokens):")
    if workflow_runs:
        lines.extend(_result_lines(workflow_runs))
    else:
        lines.append(
            "  none found (CI may not have started, or this repository does not "
            "use GitHub Actions)"
        )

    if other_checks:
        lines.append("")
        lines.append(
            "Other checks (best-effort; GitHub App tokens may not see all of these):"
        )
        lines.extend(_result_lines(other_checks))

    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  - {note}" for note in notes)

    return "\n".join(lines)


class GithubCiStatusExecutor(
    ToolExecutor[GithubCiStatusAction, GithubCiStatusObservation]
):
    """Fetch PR/commit CI status from App-token-compatible GitHub endpoints."""

    def __call__(
        self,
        action: GithubCiStatusAction,
        conversation: LocalConversation | None = None,
    ) -> GithubCiStatusObservation:
        token = _resolve_token(conversation)
        if not token:
            return GithubCiStatusObservation.from_text(
                text=(
                    "No GitHub token found. Provide one as a conversation secret "
                    "(github_token or GITHUB_TOKEN) or set the GITHUB_TOKEN "
                    "environment variable."
                ),
                is_error=True,
            )

        parts = action.repository.split("/")
        if len(parts) != 2 or not all(parts):
            return GithubCiStatusObservation.from_text(
                text=(
                    f'Invalid repository "{action.repository}"; expected "owner/repo".'
                ),
                is_error=True,
            )

        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
                return self._fetch_status(client, token, action)
        except GithubAPIError as exc:
            return GithubCiStatusObservation.from_text(text=str(exc), is_error=True)
        except httpx.HTTPError as exc:
            return GithubCiStatusObservation.from_text(
                text=f"GitHub API request failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

    def _fetch_status(
        self,
        client: httpx.Client,
        token: str,
        action: GithubCiStatusAction,
    ) -> GithubCiStatusObservation:
        repository = action.repository
        pull_number = action.pull_number

        if pull_number is not None:
            pull = _fetch_page(
                client, token, f"/repos/{repository}/pulls/{pull_number}", {}
            )
            sha = ((pull.get("head") or {}) if isinstance(pull, dict) else {}).get(
                "sha"
            )
            if not sha:
                return GithubCiStatusObservation.from_text(
                    text=f"Pull request #{pull_number} has no head commit.",
                    is_error=True,
                )
        else:
            sha = action.sha or ""

        runs = _fetch_items(
            client,
            token,
            f"/repos/{repository}/actions/runs",
            "workflow_runs",
        )
        runs = [run for run in runs if run.get("head_sha") == sha]

        workflow_runs: list[CiResult] = []
        for run in runs:
            result = CiResult(
                name=run.get("name") or "unnamed workflow",
                kind="workflow_run",
                status=run.get("status") or "",
                conclusion=run.get("conclusion"),
                url=run.get("html_url"),
            )
            needs_detail = _classify(result.status, result.conclusion) != "success"
            if action.include_jobs and needs_detail and run.get("id") is not None:
                jobs = _fetch_items(
                    client,
                    token,
                    f"/repos/{repository}/actions/runs/{run['id']}/jobs",
                    "jobs",
                )
                result = result.model_copy(update={"details": _job_failure_lines(jobs)})
            workflow_runs.append(result)

        other_checks, notes = _other_checks(client, token, repository, sha)
        overall = _overall(workflow_runs + other_checks)
        report = _format_report(
            repository,
            sha,
            pull_number,
            overall,
            workflow_runs,
            other_checks,
            notes,
        )
        return GithubCiStatusObservation.from_text(
            text=report,
            repository=repository,
            sha=sha,
            pull_number=pull_number,
            overall=overall,
            workflow_runs=workflow_runs,
            other_checks=other_checks,
            unavailable_sources=notes,
        )
