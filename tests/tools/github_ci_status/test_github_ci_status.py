"""Tests for the github_ci_status tool against a real local HTTP stub."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from openhands.sdk import LocalConversation, Tool
from openhands.sdk.agent import Agent
from openhands.sdk.testing import TestLLM
from openhands.tools.github_ci_status import (
    GithubCiStatusAction,
    GithubCiStatusExecutor,
    GithubCiStatusObservation,
    GithubCiStatusTool,
)


HEAD_SHA = "a" * 40
OTHER_SHA = "b" * 40
REPO = "octo/repo"

TOKEN_ENV_CANDIDATES = ("github_token", "GITHUB_TOKEN", "GH_TOKEN", "github")


class _StubServer(ThreadingHTTPServer):
    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.routes = routes
        self.requests: list[tuple[str, dict[str, list[str]], dict[str, str]]] = []


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        server = cast(_StubServer, self.server)
        server.requests.append(
            (parsed.path, parse_qs(parsed.query), dict(self.headers))
        )
        route = server.routes.get(parsed.path)
        status, payload = route if route else (404, {"message": "Not Found"})
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    server = _StubServer({})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setenv("GITHUB_API_URL", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _run_summary(
    name: str,
    *,
    run_id: int,
    conclusion: str | None,
    status: str = "completed",
    sha: str = HEAD_SHA,
) -> dict:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def _jobs_route(server: _StubServer, run_id: int, jobs: list[dict]) -> None:
    server.routes[f"/repos/{REPO}/actions/runs/{run_id}/jobs"] = (
        200,
        {"total_count": len(jobs), "jobs": jobs},
    )


def _execute(action: GithubCiStatusAction) -> GithubCiStatusObservation:
    return GithubCiStatusExecutor()(action)


def test_tool_name_and_registration() -> None:
    assert GithubCiStatusTool.name == "github_ci_status"
    tool = GithubCiStatusTool.create()[0]
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint
    assert not tool.annotations.destructiveHint
    assert "App-token-compatible" in tool.description
    assert "actions/runs" in tool.description


def test_tool_rejects_parameters() -> None:
    with pytest.raises(ValueError, match="does not accept parameters"):
        GithubCiStatusTool.create(repository="octo/repo")


def test_action_requires_exactly_one_target() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        GithubCiStatusAction(repository=REPO)
    with pytest.raises(ValidationError, match="exactly one"):
        GithubCiStatusAction(repository=REPO, pull_number=1, sha=HEAD_SHA)


def test_action_normalizes_repository() -> None:
    action = GithubCiStatusAction(
        repository="https://github.com/octo/repo.git", sha=HEAD_SHA
    )
    assert action.repository == "octo/repo"


def test_pr_head_resolution_failure_report_and_graceful_403s(
    api: _StubServer,
) -> None:
    api.routes[f"/repos/{REPO}/pulls/42"] = (200, {"head": {"sha": HEAD_SHA}})
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        200,
        {
            "total_count": 4,
            "workflow_runs": [
                _run_summary("unit-tests", run_id=1, conclusion="failure"),
                _run_summary("lint", run_id=2, conclusion="success"),
                _run_summary("build", run_id=3, conclusion=None, status="in_progress"),
                # Server-side head_sha filtering should be enforced client-side too.
                _run_summary(
                    "other-branch", run_id=4, conclusion="failure", sha=OTHER_SHA
                ),
            ],
        },
    )
    _jobs_route(
        api,
        1,
        [
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "failure",
                "html_url": f"https://github.com/{REPO}/actions/runs/1/job/9",
                "steps": [
                    {"name": "Checkout", "conclusion": "success"},
                    {"name": "Run tests", "conclusion": "failure"},
                ],
            }
        ],
    )
    _jobs_route(
        api,
        3,
        [{"name": "build", "status": "in_progress", "conclusion": None, "steps": []}],
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/check-runs"] = (
        403,
        {"message": "Must have read access"},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/statuses"] = (
        403,
        {"message": "Must have read access"},
    )

    observation = _execute(GithubCiStatusAction(repository=REPO, pull_number=42))

    assert not observation.is_error
    assert observation.overall == "failure"
    assert observation.sha == HEAD_SHA
    assert observation.pull_number == 42
    assert [r.name for r in observation.workflow_runs] == [
        "unit-tests",
        "lint",
        "build",
    ]
    assert observation.workflow_runs[0].details == [
        f"pytest [failure] - failed steps: Run tests - https://github.com/{REPO}/actions/runs/1/job/9"
    ]
    assert len(observation.unavailable_sources) == 2
    assert all("permission" in note for note in observation.unavailable_sources)

    text = observation.text
    assert "Overall: FAILURE" in text
    assert "[FAIL] unit-tests" in text
    assert "[PASS] lint" in text
    assert "[PENDING] build" in text
    assert "failed steps: Run tests" in text
    assert "check-runs: unavailable (HTTP 403)" in text

    requested = [path for path, _, _ in api.requests]
    assert f"/repos/{REPO}/pulls/42" in requested
    # Jobs are fetched for the failing and in-progress runs, never for the
    # passing run, and never for a run on a different head SHA.
    assert f"/repos/{REPO}/actions/runs/1/jobs" in requested
    assert f"/repos/{REPO}/actions/runs/3/jobs" in requested
    assert f"/repos/{REPO}/actions/runs/2/jobs" not in requested
    assert f"/repos/{REPO}/actions/runs/4/jobs" not in requested


def test_sha_target_skips_pull_request_lookup(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        200,
        {
            "total_count": 1,
            "workflow_runs": [_run_summary("ci", run_id=1, conclusion="success")],
        },
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/check-runs"] = (
        200,
        {"total_count": 0, "check_runs": []},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/statuses"] = (200, [])

    observation = _execute(GithubCiStatusAction(repository=REPO, sha=HEAD_SHA))

    assert not observation.is_error
    assert observation.overall == "success"
    assert observation.pull_number is None
    requested = [path for path, _, _ in api.requests]
    assert all("/pulls/" not in path for path in requested)


def test_all_green_skips_job_lookups_and_reports_success(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        200,
        {
            "total_count": 2,
            "workflow_runs": [
                _run_summary("lint", run_id=1, conclusion="success"),
                _run_summary("test", run_id=2, conclusion="skipped"),
            ],
        },
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/check-runs"] = (
        200,
        {"total_count": 0, "check_runs": []},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/statuses"] = (200, [])

    observation = _execute(
        GithubCiStatusAction(repository=REPO, sha=HEAD_SHA, include_jobs=True)
    )

    assert observation.overall == "success"
    requested = [path for path, _, _ in api.requests]
    assert all("/jobs" not in path for path in requested)
    assert "Overall: SUCCESS - 2 pass" in observation.text


def test_no_ci_signals_reports_neutral(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        200,
        {"total_count": 0, "workflow_runs": []},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/check-runs"] = (
        200,
        {"total_count": 0, "check_runs": []},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/statuses"] = (200, [])

    observation = _execute(GithubCiStatusAction(repository=REPO, sha=HEAD_SHA))

    assert observation.overall == "neutral"
    assert "Overall: NEUTRAL" in observation.text
    assert "none found" in observation.text


def test_include_jobs_false_skips_job_lookups(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        200,
        {
            "total_count": 1,
            "workflow_runs": [_run_summary("ci", run_id=1, conclusion="failure")],
        },
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/check-runs"] = (
        200,
        {"total_count": 0, "check_runs": []},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/statuses"] = (200, [])

    observation = _execute(
        GithubCiStatusAction(repository=REPO, sha=HEAD_SHA, include_jobs=False)
    )

    assert observation.overall == "failure"
    requested = [path for path, _, _ in api.requests]
    assert all("/jobs" not in path for path in requested)


def test_third_party_checks_merge_into_rollup(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        200,
        {
            "total_count": 1,
            "workflow_runs": [_run_summary("ci", run_id=1, conclusion="success")],
        },
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/check-runs"] = (
        200,
        {
            "total_count": 3,
            "check_runs": [
                {
                    "name": "actions / build",
                    "app": {"slug": "github-actions"},
                    "status": "completed",
                    "conclusion": "failure",
                },
                {
                    "name": "third-party/scan",
                    "app": {"slug": "other-app"},
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://example.test/scan",
                },
                {
                    "name": "third-party/flaky",
                    "app": {"slug": "other-app"},
                    "status": "in_progress",
                    "conclusion": None,
                },
            ],
        },
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/statuses"] = (
        200,
        [
            {
                "context": "legacy-ci",
                "state": "failure",
                "target_url": "https://example.test/legacy",
            },
            # Older update of the same context: must be ignored.
            {"context": "legacy-ci", "state": "success"},
        ],
    )

    observation = _execute(GithubCiStatusAction(repository=REPO, sha=HEAD_SHA))

    # github-actions check runs are dropped (workflow runs cover them).
    assert [r.name for r in observation.other_checks] == [
        "third-party/scan",
        "third-party/flaky",
        "legacy-ci",
    ]
    legacy = observation.other_checks[2]
    assert legacy.status == "failure"  # Newest status update per context wins.
    assert observation.overall == "failure"
    assert "third-party/scan" in observation.text
    assert "legacy-ci" in observation.text


def test_missing_token_is_error_observation(
    api: _StubServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in TOKEN_ENV_CANDIDATES:
        monkeypatch.delenv(name, raising=False)

    observation = _execute(GithubCiStatusAction(repository=REPO, sha=HEAD_SHA))

    assert observation.is_error
    assert "No GitHub token found" in observation.text
    assert api.requests == []


def test_api_404_becomes_error_observation(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/pulls/404"] = (404, {"message": "Not Found"})

    observation = _execute(GithubCiStatusAction(repository=REPO, pull_number=404))

    assert observation.is_error
    assert "HTTP 404" in observation.text
    assert "Not found" in observation.text


def test_actions_api_403_names_the_missing_permission(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        403,
        {"message": "Must have read access"},
    )

    observation = _execute(GithubCiStatusAction(repository=REPO, sha=HEAD_SHA))

    assert observation.is_error
    assert "actions: read" in observation.text


def test_invalid_repository_rejected(api: _StubServer) -> None:
    observation = _execute(
        GithubCiStatusAction.model_construct(
            repository="just-a-name", pull_number=1, sha=None, include_jobs=True
        )
    )
    assert observation.is_error
    assert 'expected "owner/repo"' in observation.text


def test_conversation_secret_wins_over_environment(api: _StubServer) -> None:
    api.routes[f"/repos/{REPO}/actions/runs"] = (
        200,
        {"total_count": 0, "workflow_runs": []},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/check-runs"] = (
        200,
        {"total_count": 0, "check_runs": []},
    )
    api.routes[f"/repos/{REPO}/commits/{HEAD_SHA}/statuses"] = (200, [])

    conversation = LocalConversation(
        agent=Agent(
            llm=TestLLM.from_messages([], model="default-model", usage_id="default"),
            tools=[Tool(name=GithubCiStatusTool.name)],
            include_default_tools=[],
        ),
        workspace=Path.cwd(),
    )
    conversation._ensure_agent_ready()
    conversation.update_secrets({"github_token": "registry-token"})

    observation = GithubCiStatusExecutor()(
        GithubCiStatusAction(repository=REPO, sha=HEAD_SHA),
        conversation=conversation,
    )

    assert not observation.is_error
    auth_headers = [headers.get("Authorization") for _, _, headers in api.requests]
    assert auth_headers and all(h == "Bearer registry-token" for h in auth_headers)
