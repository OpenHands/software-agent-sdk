---
name: sdk-release
description: >-
  This skill should be used when the user asks to "release the SDK",
  "prepare a release", "publish a new version", "cut a release",
  "do a release", or mentions the SDK release checklist or release process.
  Guides through the full software-agent-sdk release workflow
  from version bump to PyPI publication, emphasizing human checkpoints.
---

# SDK Release Guide

This skill walks through the software-agent-sdk release process step by step.

> **🚨 CRITICAL**: NEVER merge the release PR or create/publish a GitHub
> release without the human's explicit approval. Release is the last line
> of human defense. Always present the current status and ask for
> confirmation before performing any irreversible action.

## Phase 1: Trigger the Prepare-Release Workflow

Determine the target version (SemVer `X.Y.Z`). Then trigger the
`prepare-release.yml` workflow, which creates a release branch and PR
automatically.

### Via GitHub UI

Navigate to
<https://github.com/OpenHands/software-agent-sdk/actions/workflows/prepare-release.yml>,
click **Run workflow**, enter the version (e.g. `1.16.0`), and run it.

### Via GitHub API

```bash
curl -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/workflows/prepare-release.yml/dispatches" \
  -d '{
    "ref": "main",
    "inputs": {
      "version": "1.16.0"
    }
  }'
```

The workflow will:
1. Validate version format
2. Create branch `rel-<version>`
3. Run `make set-package-version version=<version>` across all packages
4. Update the `sdk_ref` default in the eval workflow
5. Open a PR titled **"Release v\<version\>"** with labels
   `integration-test`, `behavior-test`, and `test-examples`

### ⏸ Checkpoint — Confirm PR Created

Verify the PR exists and the version changes look correct before continuing.

```bash
gh pr list --repo OpenHands/software-agent-sdk \
  --head "rel-<version>" --json number,title,url
```

## Phase 2: Address Deprecation Deadlines

The `deprecation-check` CI job runs on every PR. If the release version
crosses any deprecation deadline declared in the codebase, the check will
fail.

Review the failing check output and either:
- Remove the deprecated code if the deadline has passed, **or**
- Extend the deadline with justification.

Push fixes to the release branch. The check must pass before merging.

## Phase 3: Wait for CI — Tests Must Pass

The release PR triggers three labeled test suites. **All three must pass.**

| Label | Suite | What it covers |
|-------|-------|----------------|
| `integration-test` | Integration tests | End-to-end agent scenarios |
| `behavior-test` | Behavior tests | Agent behavioral guardrails |
| `test-examples` | Example tests | All runnable examples in `examples/` |

Monitor status:

```bash
gh pr checks <PR_NUMBER> --repo OpenHands/software-agent-sdk
```

### ⏸ Checkpoint — Human Judgment on Failures

Some test failures may be pre-existing or flaky. Decide with the team
whether each failure is:
- **Blocking** — must fix before release
- **Known / pre-existing** — acceptable to release with a follow-up issue
- **Flaky** — re-run the workflow

Re-run failed jobs:

```bash
# Find the run ID
gh run list --repo OpenHands/software-agent-sdk \
  --branch "rel-<version>" --limit 5

# Re-run failed jobs
gh run rerun <RUN_ID> --repo OpenHands/software-agent-sdk --failed
```

## Phase 4: Run Evaluation (Optional but Recommended)

Trigger an evaluation run on SWE-bench (or another benchmark) against the
release branch to catch regressions. See the `run-eval` skill for full
details.

```bash
curl -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/workflows/run-eval.yml/dispatches" \
  -d '{
    "ref": "main",
    "inputs": {
      "benchmark": "swebench",
      "sdk_ref": "v<version>",
      "eval_limit": "50",
      "reason": "Pre-release eval for v<version>",
      "allow_unreleased_branches": "true"
    }
  }'
```

### ⏸ Checkpoint — Evaluate Results

Compare the eval results against the previous release. Significant score
drops should block the release.

## Phase 5: Merge the Release PR

> **🚨 STOP — Do NOT merge without explicit human approval.**
> Present the CI status summary and ask the human to confirm before merging.

Once the human approves:

```bash
gh pr merge <PR_NUMBER> --repo OpenHands/software-agent-sdk --merge
```

## Phase 6: Create and Publish the GitHub Release

> **🚨 STOP — Do NOT create or publish a release without explicit human
> approval.** Publishing is irreversible — it triggers PyPI publication.
> Present the release details and ask the human to confirm.

Navigate to <https://github.com/OpenHands/software-agent-sdk/releases/new>
and:

1. **Tag**: `v<version>` (create new tag)
2. **Target branch**: `rel-<version>` (or `main` after merge)
3. Click **Auto-generate release notes**
4. Review the notes, then click **Publish release**

Publishing the release automatically triggers the `pypi-release.yml`
workflow, which builds and publishes all four packages to PyPI:
- `openhands-sdk`
- `openhands-tools`
- `openhands-workspace`
- `openhands-agent-server`

### ⏸ Checkpoint — Verify PyPI Publication

```bash
# Check each package is available
for pkg in openhands-sdk openhands-tools openhands-workspace openhands-agent-server; do
  curl -s -o /dev/null -w "$pkg: %{http_code}\n" \
    "https://pypi.org/pypi/$pkg/<version>/json"
done
```

All should return `200`. Allow a few minutes for PyPI indexing.

## Phase 7: Post-Release — Notify the Team

After PyPI publication, the `version-bump-prs.yml` workflow automatically
creates downstream version bump PRs. Compose a Slack message for the human
to post, including links to those PRs:

```
🚀 *SDK v<version> published to PyPI!*

Version bump PRs:
• <https://github.com/All-Hands-AI/OpenHands/pulls?q=is%3Apr+bump-sdk-<version>|OpenHands>
• <https://github.com/OpenHands/openhands-cli/pulls?q=is%3Apr+bump-sdk-<version>|OpenHands-CLI>

Release: <https://github.com/OpenHands/software-agent-sdk/releases/tag/v<version>|v<version>>
```

See `references/post-release-checklist.md` for details on reviewing
downstream PRs and handling any issues.

## Quick Reference — Full Checklist

- [ ] Trigger `prepare-release.yml` with target version
- [ ] Verify release PR is created
- [ ] Fix deprecation deadline failures (if any)
- [ ] Integration tests pass
- [ ] Behavior tests pass
- [ ] Example tests pass
- [ ] (Optional) Evaluation run shows no regressions
- [ ] **🚨 Get human approval**, then merge the release PR
- [ ] **🚨 Get human approval**, then create GitHub release with tag `v<version>`
- [ ] Auto-generate release notes and publish
- [ ] Verify packages appear on PyPI
- [ ] Send Slack message with downstream version bump PR links
