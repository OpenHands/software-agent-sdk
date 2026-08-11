from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


# Reject placeholders while allowing a concise human-written sentence.
MIN_HUMAN_NOTE_CHARS = 20
# These are the only PR-template sections that must remain and contain content.
REQUIRED_TEMPLATE_FIELDS: tuple[str, ...] = ("Why", "Summary", "How to Test")

HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
HUMAN_HEADING_RE = re.compile(r"(?im)^\s*HUMAN:\s*$")
AGENT_HEADING_RE = re.compile(r"(?im)^\s*AGENT:\s*$")
ISSUE_REF_RE = re.compile(r"(?i)(?:fix|clos|resolv)(?:e?(?:s|d)?|ing)?\s+#(\d+)")
BARE_ISSUE_REF_RE = re.compile(r"(?<!\w)#(\d+)")
READY_FOR_DEV_LABEL = "ready-for-dev"


def visible_text(text: str) -> str:
    """Return PR body content that should count as author-provided text."""
    lines = []
    for line in HTML_COMMENT_RE.sub("", text).splitlines():
        stripped = line.strip()
        if stripped and stripped != "-":
            lines.append(stripped)
    return "\n".join(lines).strip()


def first_visible_line(text: str) -> str:
    for line in HTML_COMMENT_RE.sub("", text).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def extract_sections(body: str) -> dict[str, str]:
    matches = list(HEADING_RE.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[start:end]
    return sections


def extract_human_note(body: str) -> str:
    """Return human-written text in the required location before AGENT."""
    human_match = HUMAN_HEADING_RE.search(body)
    if human_match is None:
        return ""

    agent_match = AGENT_HEADING_RE.search(body, human_match.end())
    if agent_match is None:
        return ""

    return visible_text(body[human_match.end() : agent_match.start()])


def extract_linked_issue_numbers(body: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for match in ISSUE_REF_RE.finditer(body):
        number = int(match.group(1))
        if number not in seen:
            numbers.append(number)
            seen.add(number)

    sections = extract_sections(body)
    issue_section = sections.get("Issue Number", "")
    for match in BARE_ISSUE_REF_RE.finditer(issue_section):
        number = int(match.group(1))
        if number not in seen:
            numbers.append(number)
            seen.add(number)
    return numbers


def fetch_issue_labels(repo: str, issue_number: int, token: str) -> list[str]:
    import urllib.request

    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{issue_number}/labels",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request) as response:  # noqa: S310 - trusted HTTPS API
        labels = json.loads(response.read().decode())
    return [label["name"] for label in labels if isinstance(label, dict)]


def validate_linked_issue_ready(
    body: str, repo: str | None = None, token: str | None = None
) -> list[str]:
    numbers = extract_linked_issue_numbers(body)
    if not numbers:
        return [
            "Link an issue in the `## Issue Number` section (e.g. `Fixes #123`). "
            "The issue must carry the `ready-for-dev` label."
        ]
    if not repo or not token:
        return []

    import urllib.error

    checked: list[int] = []
    for number in numbers:
        try:
            labels = fetch_issue_labels(repo, number, token)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        checked.append(number)
        if READY_FOR_DEV_LABEL in [label.lower() for label in labels]:
            return []

    if checked:
        refs = ", ".join(f"#{number}" for number in checked)
        return [
            f"None of the linked issues ({refs}) carry the `ready-for-dev` label. "
            "The issue must meet the readiness criteria before a PR can be opened."
        ]
    return [f"Referenced issue(s) {', '.join(f'#{n}' for n in numbers)} could not be found in this repository."]


def validate_pr_body(body: str) -> list[str]:
    errors: list[str] = []

    if first_visible_line(body) != "HUMAN:":
        errors.append("The first visible line of the PR description must be `HUMAN:`.")

    human_note = extract_human_note(body)
    if len(human_note) < MIN_HUMAN_NOTE_CHARS:
        errors.append("Add a short human-written note between `HUMAN:` and `AGENT:`.")

    if AGENT_HEADING_RE.search(body) is None:
        errors.append("Keep the `AGENT:` marker from the PR template.")

    sections = extract_sections(body)
    for section in REQUIRED_TEMPLATE_FIELDS:
        if section not in sections:
            errors.append(f"Keep the `## {section}` section from the PR template.")
        elif not visible_text(sections[section]):
            errors.append(f"Fill in the `## {section}` section of the PR template.")

    return errors


def body_from_event(event_path: Path) -> str:
    payload = json.loads(event_path.read_text())
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("GitHub event payload does not contain a pull_request object")
    body = pull_request.get("body")
    return body if isinstance(body, str) else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate pull request description readiness from --body-file "
            "or a GitHub event payload."
        )
    )
    parser.add_argument(
        "--body-file", type=Path, help="Read a PR description body from a file."
    )
    parser.add_argument(
        "--event-path",
        type=Path,
        default=Path(os.environ["GITHUB_EVENT_PATH"])
        if "GITHUB_EVENT_PATH" in os.environ
        else None,
        help="Read the PR description body from a GitHub event payload.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.body_file is not None:
        body = args.body_file.read_text()
    elif args.event_path is not None:
        body = body_from_event(args.event_path)
    else:
        raise SystemExit("Pass --body-file or set GITHUB_EVENT_PATH.")

    errors = validate_pr_body(body)

    repo = None
    token = os.environ.get("GITHUB_TOKEN")
    if args.event_path is not None and args.body_file is None:
        payload = json.loads(args.event_path.read_text())
        repo = payload.get("repository", {}).get("full_name")
    errors.extend(validate_linked_issue_ready(body, repo, token))

    for error in errors:
        print(f"::error::{error}")

    if errors:
        print(f"PR description validation failed with {len(errors)} error(s).")
        return 1

    print("PR description validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
