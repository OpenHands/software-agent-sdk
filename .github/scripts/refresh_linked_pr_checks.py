from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from check_pr_description import extract_linked_issue_numbers


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, check=False
    )


def _linked_open_prs(repo: str, issue_number: int) -> list[dict]:
    """Return open PRs that cross-reference ``issue_number``."""
    owner, name = repo.split("/", 1)
    query = """
query($owner: String!, $name: String!, $num: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $num) {
      timelineItems(first: 100, itemTypes: [CROSS_REFERENCED_EVENT]) {
        nodes {
          __typename
          ... on CrossReferencedEvent {
            source {
              __typename
              ... on PullRequest { number headRefOid state }
            }
          }
        }
      }
    }
  }
}
"""
    result = _run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"num={issue_number}",
            "--jq",
            ".data.repository.issue.timelineItems.nodes",
        ]
    )
    if result.returncode != 0:
        print(f"::warning::Could not query linked PRs: {result.stderr.strip()}")
        return []
    try:
        nodes = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"::warning::Unexpected linked-PR query output: {exc}")
        return []
    prs = []
    for node in nodes:
        source = node.get("source") if isinstance(node, dict) else None
        if not isinstance(source, dict):
            continue
        if source.get("__typename") != "PullRequest":
            continue
        if source.get("state") != "OPEN":
            continue
        prs.append(source)
    return prs


def _rerun_pr_description_check(repo: str, head_sha: str) -> bool:
    """Re-run the latest PR Description Check for the given head SHA."""
    runs_result = _run(
        [
            "gh",
            "api",
            f"repos/{repo}/actions/runs",
            "-f",
            f"head_sha={head_sha}",
            "-f",
            "per_page=100",
            "--jq",
            r'.workflow_runs[] | select(.name=="PR Description Check") | '
            r'select(.event=="pull_request_target") | "\(.id) \(.created_at)"',
        ]
    )
    if runs_result.returncode != 0:
        print(f"::warning::Could not list PR checks: {runs_result.stderr.strip()}")
        return False
    lines = [line.split() for line in runs_result.stdout.splitlines() if line.strip()]
    if not lines:
        return False
    # Created-at is sortable; pick the most recent run for this commit.
    latest = sorted(lines, key=lambda item: " ".join(item[1:]))[-1][0]
    rerun = _run(
        ["gh", "api", "-X", "POST", f"repos/{repo}/actions/runs/{latest}/rerun"]
    )
    if rerun.returncode != 0:
        print(
            f"::warning::Could not re-run PR description check ({latest}): "
            f"{rerun.stderr.strip()}"
        )
        return False
    print(f"Re-ran PR description check (run {latest}) for linked open PR.")
    return True


def main() -> int:
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if {"action", "issue", "label", "repository"} - set(payload):
        return 0
    action = payload["action"]
    raw_label = payload.get("label")
    label = raw_label.get("name") if isinstance(raw_label, dict) else None
    if action not in ("labeled", "unlabeled") or label != "ready-for-dev":
        return 0
    raw_repo = payload.get("repository")
    repo = raw_repo.get("full_name") if isinstance(raw_repo, dict) else None
    raw_issue = payload.get("issue")
    issue_number = raw_issue.get("number") if isinstance(raw_issue, dict) else None
    if not repo or not issue_number:
        return 0

    print(
        f"ready-for-dev {action}: refreshing PR gates linked to issue #{issue_number}."
    )
    refreshed = 0
    for pr in _linked_open_prs(repo, issue_number):
        pr_number = pr["number"]
        body_result = _run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo,
                "--json",
                "body",
                "--jq",
                ".body",
            ]
        )
        if body_result.returncode != 0:
            continue
        if issue_number not in extract_linked_issue_numbers(body_result.stdout):
            # Cross-referenced but not treated as a linked issue by the gate;
            # nothing to refresh.
            continue
        if _rerun_pr_description_check(repo, pr["headRefOid"]):
            refreshed += 1
    print(f"Refreshed PR gates for {refreshed} linked PR(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())