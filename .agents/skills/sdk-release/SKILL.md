---
name: sdk-release
description: >-
  This skill should be used when the user asks to "release the SDK",
  "prepare a release", "publish a new version", "cut a release",
  "do a release", or mentions the SDK release checklist or release process.
  Guides through the full software-agent-sdk release workflow
  from version bump to PyPI publication, emphasizing human checkpoints.
triggers:
- release SDK
- prepare release
- publish version
- cut release
- release checklist
- new SDK version
---

# SDK Release Guide

This skill walks through the software-agent-sdk release process step by step.
**Important**: Releasing is a human-supervised process. Each phase has decision
points that require human judgment — do not proceed past a checkpoint without
explicit confirmation.

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

Once all checks pass and the team is satisfied:

```bash
gh pr merge <PR_NUMBER> --repo OpenHands/software-agent-sdk --merge
```

## Phase 6: Create and Publish the GitHub Release

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

## Phase 7: Post-Release (Automated)

After PyPI publication, the `version-bump-prs.yml` workflow automatically:

1. Creates a version bump PR on **OpenHands/openhands-cli**
2. Creates a version bump PR on **All-Hands-AI/OpenHands**
3. Posts a notification to the `#agent-team` Slack channel

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
- [ ] Merge the release PR
- [ ] Create GitHub release with tag `v<version>`
- [ ] Auto-generate release notes and publish
- [ ] Verify packages appear on PyPI
- [ ] Review downstream version bump PRs (automated)
