"""Release security check #1 — approval drift (a TOCTOU guard).

The window we care about: a human reviews a PR (time-of-check), but what is
actually merged and shipped (time-of-use) is not what they reviewed — because
commits were pushed after approval, or the PR was merged with no human approval
at all (e.g. auto-merge, or an agent acting on an injected review comment).

For every PR merged into this line of history since the last release tag, this
compares the PR's merged head against its last *human* APPROVED review:

  * merged with **no human approval**            -> blocking
  * commits pushed **after** the last approval    -> blocking (the reviewed
    diff is not the shipped diff)

Deterministic: reads ``git log`` for the merged-PR list and the read-only
GitHub REST API for reviews. It never runs the PR's code.

Env:
  GITHUB_TOKEN       (recommended; raises rate limits, reads private repos)
  GITHUB_REPOSITORY  (owner/name; falls back to the origin remote)
  SECURITY_SCAN_BASELINE_TAG  (optional; overrides the auto-detected last tag)
  SECURITY_SCAN_TRUSTED_BOTS  (optional CSV of bot logins whose merges are
                               allowed without human approval, e.g. dependabot)
"""

from __future__ import annotations

import os
import re
import sys

from security_scan_common import (
    Report,
    github_request,
    github_request_all,
    last_release_tag,
    resolve_repo,
    run_git,
)


# Squash-merge subjects on this repo end with "(#1234)"; that is the PR number.
_PR_NUM_RE = re.compile(r"\(#(\d+)\)\s*$")

# GitHub marks these association values as coming from outside the project.
_OUTSIDE_ASSOC = {"NONE", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN"}


def merged_pr_numbers(baseline: str) -> tuple[list[tuple[int, str]], list[str]]:
    """PRs named in ``baseline..HEAD`` plus the subjects that map to none.

    Returns ``(matched, unmapped)`` where ``matched`` is ``(pr_number,
    subject)`` for first-parent commits whose subject ends in ``(#N)``, and
    ``unmapped`` is the subjects that do not — so the caller can surface the
    blind spot (a merge that skipped the squash convention is exactly the one
    an approval-drift guard must not silently ignore) instead of dropping it.
    """
    log = run_git(
        "log", "--first-parent", "--pretty=format:%H%x1f%s", f"{baseline}..HEAD"
    )
    matched: list[tuple[int, str]] = []
    unmapped: list[str] = []
    for line in log.splitlines():
        if "\x1f" not in line:
            continue
        _, subject = line.split("\x1f", 1)
        match = _PR_NUM_RE.search(subject)
        if match:
            matched.append((int(match.group(1)), subject))
        else:
            unmapped.append(subject)
    return matched, unmapped


def _is_bot(user: dict[str, object]) -> bool:
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def audit_pr(
    repo: str, number: int, token: str | None, trusted_bots: set[str]
) -> tuple[str, str | None]:
    """Return (status, detail) for one PR.

    status is one of: "ok", "no-human-approval", "changed-after-approval",
    "trusted-bot", "error".
    """
    try:
        pr = github_request(f"/repos/{repo}/pulls/{number}", token)
        # Paginate: the reviews endpoint returns every review (COMMENTED too),
        # so a heavily-discussed PR can exceed one page. A human APPROVED past
        # the first page must not be missed, or the PR is misclassified as
        # unapproved and blocks a clean release.
        reviews = github_request_all(
            f"/repos/{repo}/pulls/{number}/reviews?per_page=100", token
        )
    except Exception as exc:  # noqa: BLE001 - report, do not crash the gate
        return "error", str(exc)

    merge_sha = pr.get("merge_commit_sha")
    head_sha = (pr.get("head") or {}).get("sha")
    author = (pr.get("user") or {}).get("login", "?")

    approvals = [
        r
        for r in reviews
        if r.get("state") == "APPROVED"
        and not _is_bot(r.get("user") or {})
        and (r.get("author_association") or "NONE") not in _OUTSIDE_ASSOC
    ]

    if not approvals:
        merged_by = pr.get("merged_by") or {}
        if _is_bot(merged_by) and merged_by.get("login") in trusted_bots:
            return "trusted-bot", f"merged by trusted bot {merged_by.get('login')}"
        return (
            "no-human-approval",
            f"author @{author}; merged={_short(merge_sha)}; no human APPROVED review",
        )

    # The reviewed commit is the head each approval was submitted against.
    approved_shas = {r.get("commit_id") for r in approvals if r.get("commit_id")}
    reviewed_head = head_sha in approved_shas
    if not reviewed_head:
        latest = approvals[-1]
        reviewer = (latest.get("user") or {}).get("login", "?")
        approved_on = _short(latest.get("commit_id"))
        return (
            "changed-after-approval",
            (
                f"last approval by @{reviewer} was on {approved_on}, but merged "
                f"head was {_short(head_sha)} — commits landed after review"
            ),
        )

    return "ok", None


def _short(sha: object) -> str:
    return str(sha)[:9] if sha else "?"


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = resolve_repo()
    baseline = os.environ.get("SECURITY_SCAN_BASELINE_TAG") or last_release_tag()
    trusted_bots = {
        b.strip()
        for b in os.environ.get("SECURITY_SCAN_TRUSTED_BOTS", "").split(",")
        if b.strip()
    }

    report = Report("🔒 Approval drift (time-of-check vs time-of-use)")

    if not baseline:
        report.add("No previous `v*` release tag found; nothing to compare against.")
        print(report.render())
        return 0

    try:
        run_git("rev-parse", "--verify", f"{baseline}^{{commit}}")
    except Exception:  # noqa: BLE001
        report.warn("baseline unavailable")
        report.add(
            f"Baseline tag `{baseline}` is not present locally "
            "(fetch tags with `fetch-depth: 0`). Skipping."
        )
        print(report.render())
        return 0

    prs, unmapped = merged_pr_numbers(baseline)
    report.add(f"Baseline: `{baseline}` — {len(prs)} merged PR(s) in range.")
    if unmapped:
        report.warn(f"{len(unmapped)} commit(s) not mapped to a PR")
        report.add(
            f"> ⚠️ {len(unmapped)} first-parent commit(s) in range did not match "
            "the `(#N)` squash convention and were **not** audited (a merge/rebase "
            "merge, or a lost `(#N)` suffix). These are a blind spot — inspect them:"
        )
        for subject in unmapped[:20]:
            report.add(f">   - {subject}")
        if len(unmapped) > 20:
            report.add(f">   - …and {len(unmapped) - 20} more")
    report.add("")

    blocking_rows: list[str] = []
    ok = 0
    errors = 0
    for number, _subject in prs:
        status, detail = audit_pr(repo, number, token, trusted_bots)
        if status == "ok":
            ok += 1
        elif status == "trusted-bot":
            ok += 1
        elif status == "error":
            errors += 1
            report.warn(f"could not audit #{number}")
        else:
            blocking_rows.append(f"| #{number} | `{status}` | {detail} |")
            report.block(f"#{number}: {status}")

    if blocking_rows:
        report.add("| PR | finding | detail |")
        report.add("| -- | ------- | ------ |")
        report.lines.extend(blocking_rows)
        report.add("")
    report.add(
        f"Audited {len(prs)} PR(s): {ok} clean, {len(blocking_rows)} flagged, "
        f"{errors} un-auditable."
    )
    if errors:
        report.add(
            "\n> Un-auditable PRs are reported as warnings, not blockers, so a "
            "transient API error does not wedge a release."
        )

    sys.stdout.write(report.render())
    _emit_summary_file(report)
    return 1 if report.blocking else 0


def _emit_summary_file(report: Report) -> None:
    out = os.environ.get("SECURITY_SCAN_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(report.render())
            handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
